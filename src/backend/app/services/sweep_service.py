"""Walk-forward formula sweeps, run as background jobs.

Sweeping is easy to abuse: try enough formulas and one will look excellent by
chance. So this service always computes the noise floor FIRST and reports the
selection diagnostics alongside every winner. The API cannot return a winner
without them, which keeps the UI from showing an encouraging number on its own.
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging
import threading
import time

import numpy as np

from ..backtest import panel as panel_mod
from ..backtest import runs as runs_repo
from ..backtest import sweep as sweep_mod
from ..domain import scoring_formula
from ..schemas.backtest import (
    NoiseFloor,
    RunProgress,
    RunStatus,
    SweepRecord,
    SweepResult,
    SweepResultRow,
    SweepRunRequest,
    SweepVerdict,
)
from .errors import StepNotReady

log = logging.getLogger(__name__)

_CANCEL: dict[str, threading.Event] = {}
_LOCK = threading.Lock()

VOL_DENOMINATORS = (("3 Month", "1 Month"), ("3 Month",), ("6 Month",), ())


def _formula(w6: int, w3: int, w1: int, vols: tuple[str, ...]) -> str:
    """Build a formula in the app's own DSL, so a winner is directly usable."""
    num = " + ".join(f"({w}% * {lab} Performance)"
                     for w, lab in ((w6, "6 Month"), (w3, "3 Month"), (w1, "1 Month")) if w)
    den = " + ".join(f"{v} Volatility" for v in vols)
    return f"({num}) / ({den})" if den else f"({num})"


def build_grid(step: int) -> list[str]:
    out = []
    for w6 in range(0, 101, step):
        for w3 in range(0, 101 - w6, step):
            for vols in VOL_DENOMINATORS:
                out.append(_formula(w6, w3, 100 - w6 - w3, vols))
    return list(dict.fromkeys(out))


# --- persistence (reuses the runs repository's atomic writes) ---------------

def _path(sweep_id: str):
    from ..backtest import config as bt_config
    return bt_config.RUNS_DIR / f"sweep-{sweep_id}.json"


def _result_path(sweep_id: str):
    from ..backtest import config as bt_config
    return bt_config.RESULTS_DIR / f"sweep-{sweep_id}.json"


def save_record(rec: SweepRecord) -> SweepRecord:
    from ..backtest import config as bt_config
    bt_config.ensure_dirs()
    runs_repo._write_atomic(_path(rec.sweep_id), rec.model_dump_json(indent=2))
    return rec


def _read(path, model):
    """Read a record the worker may be rewriting at this very moment.

    A live sweep replaces its record every few formulas while the UI polls it.
    On Windows a read can land in the middle of that replace and fail; one
    failed read used to be reported as "no such sweep" — a spurious 404 for a
    sweep that was running normally. So a failure is retried briefly, exactly
    as `runs.load_run` does for backtests, and only a file that stays missing
    or unreadable is reported as absent.
    """
    for _ in range(runs_repo._RETRIES):
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — missing, mid-replace, or corrupt
            time.sleep(runs_repo._BACKOFF)
    return None


def get_sweep(sweep_id: str) -> SweepRecord | None:
    return _read(_path(sweep_id), SweepRecord)


def list_sweeps(limit: int | None = None) -> list[SweepRecord]:
    from ..backtest import config as bt_config
    if not bt_config.RUNS_DIR.exists():
        return []
    out = []
    for p in sorted(bt_config.RUNS_DIR.glob("sweep-*.json"), reverse=True):
        rec = get_sweep(p.stem[len("sweep-"):])
        if rec:
            out.append(rec)
    return out[:limit] if limit else out


def get_result(sweep_id: str) -> SweepResult | None:
    return _read(_result_path(sweep_id), SweepResult)


def delete_sweep(sweep_id: str) -> bool:
    cancel_sweep(sweep_id)
    found = False
    for p in (_path(sweep_id), _result_path(sweep_id)):
        if p.exists():
            p.unlink()
            found = True
    return found


def cancel_sweep(sweep_id: str) -> bool:
    with _LOCK:
        ev = _CANCEL.get(sweep_id)
    if ev is None:
        return False
    ev.set()
    return True


# --- running ----------------------------------------------------------------

def start_sweep(body: SweepRunRequest, now: dt.datetime | None = None) -> SweepRecord:
    now = now or dt.datetime.now()
    from ..backtest import universe
    if not universe.load(body.universe_id):
        raise StepNotReady(f"Universe {body.universe_id!r} is empty — build it first.")
    if body.test_start <= body.train_end:
        raise StepNotReady(
            "The test period must start after the training period ends, or the "
            "'out-of-sample' result is not out of sample.")

    rec = SweepRecord(
        sweep_id=runs_repo.new_run_id(now), label=body.label, status=RunStatus.QUEUED,
        created_at=now, updated_at=now, request=body.model_dump(mode="json"),
        progress=RunProgress(detail="queued"),
    )
    save_record(rec)

    cancel = threading.Event()
    with _LOCK:
        _CANCEL[rec.sweep_id] = cancel
    threading.Thread(target=_execute, args=(rec.sweep_id, body, cancel),
                     name=f"sweep-{rec.sweep_id}", daemon=True).start()
    return rec


class _Cancelled(RuntimeError):
    pass


def _touch(rec: SweepRecord, **fields) -> SweepRecord:
    for k, v in fields.items():
        setattr(rec, k, v)
    rec.updated_at = dt.datetime.now()
    return save_record(rec)


def _execute(sweep_id: str, body: SweepRunRequest, cancel: threading.Event) -> None:
    rec = get_sweep(sweep_id)
    if rec is None:
        return
    try:
        _touch(rec, status=RunStatus.FETCHING,
               progress=RunProgress(detail="building the feature panel (once per universe)"))
        p = panel_mod.get(
            body.universe_id, body.index_id,
            progress=lambda d, t: _touch(rec, progress=RunProgress(
                done=d, total=t, pct=round(100 * d / t, 1) if t else 0.0,
                detail=f"panel {d}/{t} symbols")))
        if cancel.is_set():
            raise _Cancelled

        regime = sweep_mod.regime_vector(p, body.filter_id)
        train, tr_mask = sweep_mod.slice_panel(p, None, body.train_end)
        test, te_mask = sweep_mod.slice_panel(p, body.test_start, None)
        r_tr, r_te = regime[tr_mask], regime[te_mask]
        if not train.weeks or not test.weeks:
            raise StepNotReady(
                "That split leaves one side empty — check the dates against the "
                "index's available history.")

        kw = dict(cost_bps=body.cost_bps, slippage_bps=body.slippage_bps)

        # --- the noise floor, BEFORE any optimisation ----------------------
        noise = NoiseFloor()
        if body.noise_samples:
            _touch(rec, status=RunStatus.RUNNING,
                   progress=RunProgress(detail="noise floor: random formulas"))
            rng = np.random.default_rng(20260724)
            vals = []
            for k in range(body.noise_samples):
                if cancel.is_set():
                    raise _Cancelled
                w = rng.dirichlet([1, 1, 1]) * 100
                f = _formula(round(w[0]), round(w[1]), round(w[2]), ("3 Month", "1 Month"))
                try:
                    vals.append(sweep_mod.evaluate(train, f, r_tr, **kw).cagr_pct)
                except Exception:  # noqa: BLE001 — a degenerate draw is not fatal
                    continue
                if k % 20 == 0:
                    _touch(rec, progress=RunProgress(
                        done=k, total=body.noise_samples,
                        pct=round(100 * k / body.noise_samples, 1),
                        detail=f"noise floor {k}/{body.noise_samples}"))
            a = np.array(vals)
            if len(a):
                noise = NoiseFloor(
                    n=len(a), min_cagr=round(float(a.min()), 2),
                    max_cagr=round(float(a.max()), 2), mean_cagr=round(float(a.mean()), 2),
                    # One draw has no spread to measure (the sample std is 0/0).
                    std_cagr=round(float(a.std(ddof=1)), 2) if len(a) > 1 else 0.0,
                    spread=round(float(a.max() - a.min()), 2))
            _touch(rec, noise=noise)

        # --- the walk-forward grid ------------------------------------------
        grid = build_grid(body.weight_step)
        rows: list[SweepResultRow] = []
        for i, f in enumerate(grid):
            if cancel.is_set():
                raise _Cancelled
            try:
                tr = sweep_mod.evaluate(train, f, r_tr, **kw)
                te = sweep_mod.evaluate(test, f, r_te, **kw)
            except Exception:  # noqa: BLE001
                continue
            rows.append(SweepResultRow(
                formula=f, train_cagr=tr.cagr_pct, train_sharpe=tr.sharpe,
                test_cagr=te.cagr_pct, test_sharpe=te.sharpe,
                test_maxdd=te.max_drawdown_pct, test_turnover=te.turnover))
            if i % 10 == 0:
                _touch(rec, status=RunStatus.RUNNING, progress=RunProgress(
                    done=i, total=len(grid), pct=round(100 * i / len(grid), 1),
                    detail=f"formula {i}/{len(grid)}"))

        if not rows:
            raise StepNotReady("No formula in the grid produced a result.")

        base = scoring_formula.DEFAULT_FORMULA
        b_tr = sweep_mod.evaluate(train, base, r_tr, **kw)
        b_te = sweep_mod.evaluate(test, base, r_te, **kw)

        te_all = np.array([r.test_cagr for r in rows])
        tr_all = np.array([r.train_cagr for r in rows])
        winner = max(rows, key=lambda r: r.train_cagr)     # chosen on TRAIN only

        # If every formula lands on the same CAGR — a test window that is RED
        # throughout buys nothing, so they all score zero — the correlation is
        # 0/0. That NaN used to be written into the record, which then failed to
        # parse and made the finished sweep disappear. No spread, no relationship.
        corr = 0.0
        if len(rows) > 2:
            with np.errstate(invalid="ignore", divide="ignore"):
                corr = float(np.corrcoef(tr_all, te_all)[0, 1])
            if not np.isfinite(corr):
                corr = 0.0

        verdict = SweepVerdict(
            baseline_formula=base,
            baseline_train_cagr=b_tr.cagr_pct, baseline_test_cagr=b_te.cagr_pct,
            winner_formula=winner.formula,
            winner_train_cagr=winner.train_cagr, winner_test_cagr=winner.test_cagr,
            winner_test_percentile=round(100 * float((te_all < winner.test_cagr).mean()), 1),
            winner_train_edge=round(winner.train_cagr - b_tr.cagr_pct, 2),
            winner_test_edge=round(winner.test_cagr - b_te.cagr_pct, 2),
            pct_beating_baseline_test=round(100 * float((te_all > b_te.cagr_pct).mean()), 1),
            median_test_cagr=round(float(np.median(te_all)), 2),
            best_possible_test_cagr=round(float(te_all.max()), 2),
            train_test_corr=round(corr, 3),
            formulas_tried=len(rows),
        )

        result = SweepResult(
            sweep_id=sweep_id,
            config={**body.model_dump(mode="json"),
                    "train_weeks": len(train.weeks), "test_weeks": len(test.weeks),
                    "train_span": [train.weeks[0].isoformat(), train.weeks[-1].isoformat()],
                    "test_span": [test.weeks[0].isoformat(), test.weeks[-1].isoformat()],
                    "universe_size": len(p.symbols)},
            noise=noise, verdict=verdict, grid=rows,
            limitations=[
                "These are SCREEN numbers. The fast simulator fills stops at the "
                "week's low, which runs a few points below the full engine — "
                "confirm any candidate with a normal backtest run before using it.",
                f"{len(rows)} formulas were compared. The best of many comparisons "
                "flatters itself; read the winner against the noise floor and its "
                "test percentile, not on its own.",
                "The test period is one sample. A different split can reorder "
                "formulas, and CAGR levels differ between periods — compare "
                "formulas WITHIN a period, never across.",
                "Survivorship bias applies as everywhere else: the universe is "
                "today's index membership applied to all of history.",
            ],
        )
        runs_repo._write_atomic(_result_path(sweep_id), result.model_dump_json())
        _touch(rec, status=RunStatus.DONE, verdict=verdict, noise=noise,
               progress=RunProgress(done=1, total=1, pct=100.0, detail="complete"))

    except _Cancelled:
        _touch(rec, status=RunStatus.CANCELLED, error="Cancelled.")
    except Exception as exc:  # noqa: BLE001 — a failed sweep is data, not a crash
        log.exception("Sweep %s failed", sweep_id)
        _touch(rec, status=RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
    finally:
        with _LOCK:
            _CANCEL.pop(sweep_id, None)
