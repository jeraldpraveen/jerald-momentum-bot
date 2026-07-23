"""Run persistence — the only layer that touches the runs/results directories.

A run record (light: config, status, progress, headline metrics) lives in
`runs/<id>.json`; the full result (curves, per-week log, every trade) lives in
`results/<id>.json`. Splitting them keeps the run LIST cheap to poll while a
job is in flight, which matters when the UI is asking every second.
"""

from __future__ import annotations

import datetime as dt
import os
import time

from ..schemas.backtest import BacktestResult, RunRecord, RunStatus
from . import config as bt_config


def _run_path(run_id: str):
    return bt_config.RUNS_DIR / f"{run_id}.json"


def _result_path(run_id: str):
    return bt_config.RESULTS_DIR / f"{run_id}.json"


def new_run_id(now: dt.datetime | None = None) -> str:
    """Timestamped id, with a suffix if two runs start in the same second."""
    now = now or dt.datetime.now()
    base = now.strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 2
    while _run_path(run_id).exists():
        run_id, n = f"{base}.{n}", n + 1
    return run_id


# A live run rewrites its record every few weeks of simulation while the UI
# polls it every second or so. On Windows a read and an os.replace can collide,
# so both sides retry briefly rather than treating a momentary clash as failure.
_RETRIES = 5
_BACKOFF = 0.05


def _write_atomic(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(_BACKOFF)


def save_run(record: RunRecord) -> RunRecord:
    bt_config.ensure_dirs()
    _write_atomic(_run_path(record.run_id), record.model_dump_json(indent=2))
    return record


def load_run(run_id: str) -> RunRecord | None:
    """The record, or None if it genuinely does not exist.

    A transient read failure (the writer is mid-replace) is retried, NOT reported
    as missing — returning None there surfaced as a spurious 404 for a run that
    was actively executing.
    """
    p = _run_path(run_id)
    for attempt in range(_RETRIES):
        if not p.exists():
            # Could be the instant between writing the temp file and replacing;
            # only believe it after a retry.
            if attempt == _RETRIES - 1:
                return None
            time.sleep(_BACKOFF)
            continue
        try:
            return RunRecord.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — mid-write, or a genuinely corrupt file
            if attempt == _RETRIES - 1:
                return None
            time.sleep(_BACKOFF)
    return None


def list_runs(limit: int | None = None) -> list[RunRecord]:
    """Newest first. Unreadable files are skipped, not fatal."""
    if not bt_config.RUNS_DIR.exists():
        return []
    out: list[RunRecord] = []
    for p in sorted(bt_config.RUNS_DIR.glob("*.json"), reverse=True):
        rec = load_run(p.stem)
        if rec is not None:
            out.append(rec)
    return out[:limit] if limit else out


def save_result(result: BacktestResult) -> None:
    bt_config.ensure_dirs()
    _write_atomic(_result_path(result.run_id), result.model_dump_json())


def load_result(run_id: str) -> BacktestResult | None:
    p = _result_path(run_id)
    if not p.exists():
        return None
    for attempt in range(_RETRIES):
        try:
            return BacktestResult.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — mid-write, or genuinely corrupt
            if attempt == _RETRIES - 1:
                return None
            time.sleep(_BACKOFF)
    return None


def delete_run(run_id: str) -> bool:
    """Remove a run and its result. Returns whether anything was there."""
    found = False
    for p in (_run_path(run_id), _result_path(run_id)):
        if p.exists():
            p.unlink()
            found = True
    return found


# How long a live-looking run may go without a heartbeat before it is presumed
# dead. Workers touch their record every few weeks of simulation and every batch
# of price downloads, so a healthy run is never quiet for anything like this.
STALE_AFTER = dt.timedelta(minutes=15)


def mark_stale_as_interrupted(now: dt.datetime | None = None) -> int:
    """Flag runs that stopped heartbeating — they can never finish.

    Judged by staleness, NOT by status alone. The naive version marked every
    live-looking run interrupted at startup, which killed runs owned by OTHER
    processes: opening a second app instance (or a test suite building a
    TestClient) reaped a backtest that was running perfectly well elsewhere.
    """
    now = now or dt.datetime.now()
    live = {RunStatus.QUEUED, RunStatus.FETCHING, RunStatus.RUNNING}
    count = 0
    for rec in list_runs():
        if rec.status not in live:
            continue
        if now - rec.updated_at.replace(tzinfo=None) < STALE_AFTER:
            continue                      # still heartbeating — leave it alone
        rec.status = RunStatus.INTERRUPTED
        rec.error = "This run stopped responding — the process was likely killed."
        rec.updated_at = now
        save_run(rec)
        count += 1
    return count
