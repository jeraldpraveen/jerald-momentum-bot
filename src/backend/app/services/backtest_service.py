"""Backtest job service — starts runs on background threads and tracks them.

A real run pulls hundreds of tickers from yfinance and walks years of weeks, so
it cannot block a request. Starting a run returns immediately with a run_id;
the UI polls the record for progress.

Nothing here touches the live cycle, ledger or journal. The only shared thing
is the strategy core, which is read-only.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading

from ..backtest import config as bt_config
from ..backtest import engine, price_store, runs, universe
from ..schemas.backtest import (
    BacktestResult,
    BacktestRunRequest,
    BuildUniverseRequest,
    IndexCoverage,
    RunProgress,
    RunRecord,
    RunStatus,
    UniverseSummary,
)
from .errors import StepNotReady

log = logging.getLogger(__name__)

# run_id -> cancel flag, for jobs alive in THIS process.
_CANCEL: dict[str, threading.Event] = {}
_LOCK = threading.Lock()


def index_coverage() -> list[IndexCoverage]:
    """Per-index history, so the UI can warn BEFORE a run is started."""
    from .. import config as app_config
    out: list[IndexCoverage] = []
    for o in app_config.INDEX_OPTIONS:
        span = price_store.coverage(o.ticker)
        first_tradable = (
            (span[0] + dt.timedelta(days=bt_config.MIN_WARMUP_DAYS)).isoformat()
            if span else None)
        out.append(IndexCoverage(
            id=o.id, label=o.label, ticker=o.ticker,
            first_session=span[0].isoformat() if span else None,
            last_session=span[1].isoformat() if span else None,
            first_tradable=first_tradable, cached=span is not None,
        ))
    return out


# --- universes --------------------------------------------------------------

def list_universes() -> list[UniverseSummary]:
    return [summarize_universe(uid) for uid in universe.list_universes()]


def summarize_universe(universe_id: str) -> UniverseSummary:
    members = universe.load(universe_id)
    cached = 0
    first: dt.date | None = None
    last: dt.date | None = None
    for m in members:
        span = price_store.coverage(m.ticker)
        if span is None:
            continue
        cached += 1
        first = span[0] if first is None or span[0] < first else first
        last = span[1] if last is None or span[1] > last else last

    return UniverseSummary(
        universe_id=universe_id, size=len(members),
        microcap_count=len(universe.microcap_symbols(members)),
        cached_prices=cached,
        first_session=first.isoformat() if first else None,
        last_session=last.isoformat() if last else None,
    )


def build_universe(body: BuildUniverseRequest) -> UniverseSummary:
    if not body.index_ids:
        raise StepNotReady("Pick at least one index to build the universe from.")
    universe.build_from_indices(body.universe_id, body.index_ids)
    return summarize_universe(body.universe_id)


def delete_universe(universe_id: str) -> bool:
    p = bt_config.UNIVERSES_DIR / f"{universe_id}.csv"
    if p.exists():
        p.unlink()
        return True
    return False


# --- runs -------------------------------------------------------------------

def list_runs(limit: int | None = None) -> list[RunRecord]:
    return runs.list_runs(limit)


def get_run(run_id: str) -> RunRecord | None:
    return runs.load_run(run_id)


def get_result(run_id: str) -> BacktestResult | None:
    return runs.load_result(run_id)


def delete_run(run_id: str) -> bool:
    cancel_run(run_id)
    return runs.delete_run(run_id)


def cancel_run(run_id: str) -> bool:
    """Ask a running job to stop at its next checkpoint."""
    with _LOCK:
        ev = _CANCEL.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def start_run(body: BacktestRunRequest, now: dt.datetime | None = None) -> RunRecord:
    """Queue a backtest and return immediately with its record."""
    now = now or dt.datetime.now()
    # Fail fast on an unusable universe rather than inside the thread.
    members = universe.load(body.universe_id)
    if not members:
        raise StepNotReady(f"Universe {body.universe_id!r} is empty — build it first.")

    record = RunRecord(
        run_id=runs.new_run_id(now), label=body.label, status=RunStatus.QUEUED,
        created_at=now, updated_at=now, request=body.model_dump(mode="json"),
        progress=RunProgress(total=0, detail="queued"),
    )
    runs.save_run(record)

    cancel = threading.Event()
    with _LOCK:
        _CANCEL[record.run_id] = cancel

    thread = threading.Thread(
        target=_execute, args=(record.run_id, body, cancel),
        name=f"backtest-{record.run_id}", daemon=True)
    thread.start()
    return record


class _Cancelled(RuntimeError):
    pass


def _touch(record: RunRecord, **fields) -> RunRecord:
    for k, v in fields.items():
        setattr(record, k, v)
    record.updated_at = dt.datetime.now()
    return runs.save_run(record)


def _execute(run_id: str, body: BacktestRunRequest, cancel: threading.Event) -> None:
    """The worker. All failures land on the record; nothing propagates."""
    record = runs.load_run(run_id)
    if record is None:
        return

    try:
        members = universe.load(body.universe_id)
        tickers = [m.ticker for m in members]
        index_ticker = _index_ticker(body.index_id)

        if body.fetch_prices:
            _touch(record, status=RunStatus.FETCHING,
                   progress=RunProgress(total=len(tickers) + 1,
                                        detail="fetching prices from yfinance"))
            def fetched(done: int, total: int) -> None:
                _touch(record, status=RunStatus.FETCHING,
                       progress=RunProgress(done=done, total=total,
                                            pct=round(100 * done / total, 1) if total else 0.0,
                                            detail=f"fetched {done}/{total} tickers"))

            alt = app_config_alt(body.alt_asset_id)
            price_store.ensure([index_ticker] + tickers + ([alt] if alt else []),
                               batch=25, progress=fetched)
            price_store.clear_memo()          # pick up whatever just landed
            if cancel.is_set():
                raise _Cancelled

        def progress(done: int, total: int, week) -> None:
            if cancel.is_set():
                raise _Cancelled
            _touch(record, status=RunStatus.RUNNING,
                   progress=RunProgress(done=done, total=total,
                                        pct=round(100 * done / total, 1) if total else 0.0,
                                        detail=f"week {week}"))

        _touch(record, status=RunStatus.RUNNING,
               progress=RunProgress(detail="walking the weeks"))

        result_dict = engine.run_backtest(
            universe_id=body.universe_id, index_id=body.index_id, filter_id=body.filter_id,
            formula=body.formula, start=body.start, end=body.end,
            starting_capital=body.starting_capital,
            position_weight_pct=body.position_weight_pct,
            stop_multiplier=body.stop_multiplier,
            cost_bps=body.cost_bps, slippage_bps=body.slippage_bps,
            rank_exit_threshold=body.rank_exit_threshold,
            veto_rank_window=body.veto_rank_window,
            alt_asset_id=body.alt_asset_id,
            alt_allocation_pct=body.alt_allocation_pct,
            progress=progress,
        )

        runs.save_result(BacktestResult(run_id=run_id, **result_dict))
        _touch(record, status=RunStatus.DONE,
               progress=RunProgress(done=1, total=1, pct=100.0, detail="complete"),
               summary={"metrics": result_dict["metrics"],
                        "benchmark": result_dict["benchmark"],
                        "trade_stats": result_dict["trade_stats"],
                        "turnover": result_dict["turnover"],
                        "config": result_dict["config"]})

    except _Cancelled:
        _touch(record, status=RunStatus.CANCELLED, error="Cancelled.")
    except Exception as exc:  # noqa: BLE001 — a failed run is data, not a crash
        log.exception("Backtest %s failed", run_id)
        _touch(record, status=RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
    finally:
        with _LOCK:
            _CANCEL.pop(run_id, None)


def _index_ticker(index_id: str) -> str:
    from .. import config as app_config
    return app_config.get_index_option(index_id).ticker


def app_config_alt(alt_id: str | None) -> str | None:
    from .. import config as app_config
    opt = app_config.get_alt_asset_option(alt_id)
    return opt.ticker if opt else None
