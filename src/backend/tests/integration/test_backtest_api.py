"""Backtest API — background runs, polling, and isolation from the live ritual."""

import threading
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.backtest import config as bt_config
from app.backtest import price_store, runs, universe
from app.main import create_app
from app.services import backtest_service
from app.schemas.backtest import RunStatus

client = TestClient(create_app())


def _series(n=900, start="2020-01-01", drift=0.0015, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.012, n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.02, "low": close * 0.98,
         "close": close, "volume": 5_000_000.0},
        index=idx,
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A temp backtest tree with a small investable world already cached."""
    root = tmp_path / "backtest"
    monkeypatch.setattr(bt_config, "BACKTEST_DIR", root)
    monkeypatch.setattr(bt_config, "PRICES_DIR", root / "prices")
    monkeypatch.setattr(bt_config, "UNIVERSES_DIR", root / "universes")
    monkeypatch.setattr(bt_config, "RUNS_DIR", root / "runs")
    monkeypatch.setattr(bt_config, "RESULTS_DIR", root / "results")
    monkeypatch.setattr(bt_config, "ALL_DIRS", [
        root, root / "prices", root / "universes", root / "runs", root / "results"])
    bt_config.ensure_dirs()
    price_store.clear_memo()

    price_store.save(app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker,
                     _series(seed=1))
    members = []
    for i in range(5):
        price_store.save(f"S{i}.NS", _series(drift=0.002 - i * 0.0004, seed=10 + i))
        members.append(universe.UniverseMember(f"S{i}", f"Sector{i % 3}", "MIDSMALLCAP400"))
    universe.save("testu", members)
    yield root

    # Join any worker still alive. monkeypatch restores bt_config on teardown, so
    # a straggler thread would otherwise write its next record into the FOLLOWING
    # test's directory (or the real one) — which is exactly the flakiness this
    # suite showed before the join was added.
    for run in backtest_service.list_runs():
        backtest_service.cancel_run(run.run_id)
    deadline = time.time() + 30
    for t in threading.enumerate():
        if t.name.startswith("backtest-"):
            t.join(timeout=max(0.0, deadline - time.time()))


def _await_run(run_id: str, timeout: float = 60.0) -> dict:
    """Poll like the UI does, until the job leaves a live state."""
    live = {RunStatus.QUEUED, RunStatus.FETCHING, RunStatus.RUNNING}
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = client.get(f"/api/backtest/runs/{run_id}").json()
        if rec["status"] not in {s.value for s in live}:
            return rec
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _start(**over) -> dict:
    body = {"universe_id": "testu", "filter_id": "always_green", "fetch_prices": False}
    body.update(over)
    r = client.post("/api/backtest/runs", json=body)
    assert r.status_code == 202, r.text
    return r.json()


# --- universes --------------------------------------------------------------

def test_list_universes_reports_cached_price_coverage(api):
    rows = client.get("/api/backtest/universes").json()
    assert len(rows) == 1
    u = rows[0]
    assert u["universe_id"] == "testu" and u["size"] == 5
    assert u["cached_prices"] == 5
    assert u["first_session"] and u["last_session"]


def test_get_unknown_universe_is_404(api):
    assert client.get("/api/backtest/universes/nope").status_code == 404


def test_build_universe_from_indices(api, monkeypatch):
    monkeypatch.setattr(
        universe.constituents, "fetch_constituents",
        lambda key: pd.DataFrame({"symbol": ["AAA", "BBB"], "sector": ["IT", "Auto"],
                                  "company": ["", ""]}))
    r = client.post("/api/backtest/universes",
                    json={"universe_id": "built", "index_ids": ["midsmall400"]})

    assert r.status_code == 201
    assert r.json()["size"] == 2
    assert "built" in universe.list_universes()


def test_build_universe_needs_an_index(api):
    r = client.post("/api/backtest/universes", json={"universe_id": "x", "index_ids": []})
    assert r.status_code == 409
    assert r.json()["error"] == "step_not_ready"


def test_delete_universe(api):
    assert client.delete("/api/backtest/universes/testu").status_code == 200
    assert client.delete("/api/backtest/universes/testu").status_code == 404


# --- runs -------------------------------------------------------------------

def test_start_run_returns_immediately_with_a_queued_record(api):
    rec = _start()
    assert rec["status"] in {"queued", "fetching", "running", "done"}
    assert rec["run_id"]
    _await_run(rec["run_id"])            # let the thread finish before teardown


def test_run_completes_and_exposes_a_result(api):
    rec = _await_run(_start(label="first")["run_id"])

    assert rec["status"] == "done", rec.get("error")
    assert rec["label"] == "first"
    assert rec["progress"]["pct"] == 100.0
    assert rec["summary"]["metrics"]["weeks"] > 0

    res = client.get(f"/api/backtest/runs/{rec['run_id']}/result").json()
    assert res["equity_curve"] and res["weeks"] and res["limitations"]
    assert res["config"]["universe_id"] == "testu"


def test_result_404s_while_a_run_has_not_produced_one(api):
    assert client.get("/api/backtest/runs/nosuch/result").status_code == 404


def test_unknown_run_is_404(api):
    assert client.get("/api/backtest/runs/nosuch").status_code == 404


def test_runs_are_listed_newest_first(api):
    a = _await_run(_start(label="a")["run_id"])
    b = _await_run(_start(label="b")["run_id"])

    ids = [r["run_id"] for r in client.get("/api/backtest/runs").json()]
    assert ids.index(b["run_id"]) < ids.index(a["run_id"])


def test_empty_universe_is_rejected_before_a_thread_starts(api):
    universe.save("empty", [])
    r = client.post("/api/backtest/runs", json={"universe_id": "empty"})
    assert r.status_code == 409
    assert "empty" in r.json()["message"]


def test_a_failed_run_records_the_error_instead_of_crashing(api):
    """A bad formula must land on the record, not 500 the API."""
    rec = _await_run(_start(formula="6 Month Performance / ")["run_id"])
    assert rec["status"] == "failed"
    assert rec["error"]


def test_delete_run_removes_record_and_result(api):
    rec = _await_run(_start()["run_id"])
    run_id = rec["run_id"]

    assert client.delete(f"/api/backtest/runs/{run_id}").status_code == 200
    assert client.get(f"/api/backtest/runs/{run_id}").status_code == 404
    assert client.get(f"/api/backtest/runs/{run_id}/result").status_code == 404
    assert client.delete(f"/api/backtest/runs/{run_id}").status_code == 404


def test_cancel_reports_whether_a_job_was_live(api):
    rec = _await_run(_start()["run_id"])
    # Already finished, so there is nothing to cancel.
    assert client.post(f"/api/backtest/runs/{rec['run_id']}/cancel").json() == {"cancelling": False}


def test_interrupted_runs_are_reaped_at_startup(api):
    """A run the server abandoned must not poll forever at 43%."""
    import datetime as dt
    rec = _await_run(_start()["run_id"])
    record = runs.load_run(rec["run_id"])
    record.status = RunStatus.RUNNING          # simulate a crash mid-run
    runs.save_run(record)

    record.updated_at = record.updated_at - runs.STALE_AFTER - dt.timedelta(minutes=1)
    runs.save_run(record)

    assert runs.mark_stale_as_interrupted() == 1
    after = runs.load_run(rec["run_id"])
    assert after.status == RunStatus.INTERRUPTED
    assert "stopped responding" in after.error


# --- isolation --------------------------------------------------------------

def test_a_backtest_never_touches_the_live_ledger_or_journal(api, tmp_path, monkeypatch):
    """The whole point of the separation: research cannot corrupt the ritual."""
    ledger = tmp_path / "holdings_ledger.csv"
    journal = tmp_path / "journal.jsonl"
    cycles = tmp_path / "cycles"
    cycles.mkdir()
    ledger.write_text("symbol,qty\nAAA,10\n", encoding="utf-8")
    journal.write_text('{"cycle_id":"x"}\n', encoding="utf-8")
    monkeypatch.setattr(app_config, "HOLDINGS_LEDGER", ledger)
    monkeypatch.setattr(app_config, "JOURNAL_JSONL", journal)
    monkeypatch.setattr(app_config, "CYCLES_DIR", cycles)

    before = (ledger.read_text(encoding="utf-8"), journal.read_text(encoding="utf-8"))
    _await_run(_start()["run_id"])

    assert (ledger.read_text(encoding="utf-8"), journal.read_text(encoding="utf-8")) == before
    assert list(cycles.iterdir()) == []


def test_backtest_endpoints_do_not_require_an_active_cycle(api):
    """The ritual's state machine is irrelevant here: whatever the cycle is
    doing (or not doing), a backtest runs."""
    assert client.get("/api/backtest/universes").status_code == 200
    rec = _await_run(_start()["run_id"])
    assert rec["status"] == "done", rec.get("error")


def test_a_healthy_run_is_not_reaped_by_another_process(api):
    """Starting a second app instance (or a test suite) must not kill a backtest
    that another process is actively running — the original reaper did exactly
    that, judging by status alone."""
    import datetime as dt
    rec = _await_run(_start()["run_id"])
    record = runs.load_run(rec["run_id"])
    record.status = RunStatus.RUNNING
    record.updated_at = dt.datetime.now()          # heartbeating right now
    runs.save_run(record)

    assert runs.mark_stale_as_interrupted() == 0
    assert runs.load_run(rec["run_id"]).status == RunStatus.RUNNING


def test_a_run_that_stopped_heartbeating_is_reaped(api):
    import datetime as dt
    rec = _await_run(_start()["run_id"])
    record = runs.load_run(rec["run_id"])
    record.status = RunStatus.RUNNING
    record.updated_at = dt.datetime.now() - runs.STALE_AFTER - dt.timedelta(minutes=1)
    runs.save_run(record)

    assert runs.mark_stale_as_interrupted() == 1
    after = runs.load_run(rec["run_id"])
    assert after.status == RunStatus.INTERRUPTED
    assert "stopped responding" in after.error
