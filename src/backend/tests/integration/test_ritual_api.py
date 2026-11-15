"""The whole Friday ritual over HTTP — every route, in the order a user hits them.

One GREEN week walked end to end through the API with the data sources faked,
plus the ledger endpoints, the error envelope, and the app start-up hook.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config
from app.backtest import runs
from app.main import app, create_app
from app.schemas.backtest import RunRecord, RunStatus
from app.services import (
    fundamental_service, holdings_service, regime_gate_service, risk_caps_service,
    scanner_service,
)
from tests.conftest import green_data
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401
from tests.unit.test_scanner_service import _ohlcv

client = TestClient(app)

HELD = [("AAA", "X"), ("BBB", "Y")]                    # seeded by the `isolated` fixture
FRESH = [("ALPHA", "Auto"), ("BETA", "Bank"), ("GAMMA", "Pharma")]
STEP = "/api/cycles/current/steps"


@pytest.fixture
def world(isolated, monkeypatch):
    """A week in which every data source answers, and answers the same way."""
    monkeypatch.setattr(config, "JOURNAL_JSONL", isolated / "journal.jsonl")
    monkeypatch.setattr(config, "JOURNAL_MD", isolated / "journal.md")
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data",
                        lambda *a, **k: green_data())

    names = [*HELD, *FRESH]
    rng = np.random.default_rng(11)
    prices = {f"{s}.NS": _ohlcv(100 * np.cumprod(1 + rng.normal(0.0008 * i, 0.012, 400)))
              for i, (s, _) in enumerate(names, start=1)}
    # One module object is shared by every service, so one patch covers them all.
    monkeypatch.setattr(scanner_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None, force=False:
                        {t: prices[t] for t in tickers if t in prices})
    monkeypatch.setattr(scanner_service, "_resolve_universes",
                        lambda ids: [(f"{s}.NS", sec, "") for s, sec in names])
    monkeypatch.setattr(fundamental_service.fundamentals, "get_interest_coverage",
                        lambda *a, **k: None)
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: {})
    monkeypatch.setattr(risk_caps_service, "_microcap_symbols", lambda: set())
    monkeypatch.setattr(holdings_service.constituents, "fetch_constituents",
                        lambda key: pd.DataFrame({"symbol": ["ALPHA"], "sector": ["Auto"]}))
    return isolated


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _run_and_advance(slug, body=None):
    ran = _ok(client.post(f"{STEP}/{slug}/run", json=body) if body is not None
              else client.post(f"{STEP}/{slug}/run"))
    advanced = _ok(client.post(f"{STEP}/{slug}/advance"))
    return ran, advanced


# --- health + start-up -----------------------------------------------------------

def test_health():
    assert _ok(client.get("/api/health")) == {"status": "ok"}


def test_startup_flags_backtests_that_died_mid_run_and_leaves_live_ones(bt_dirs):  # noqa: F811
    now = dt.datetime.now()
    dead = RunRecord(run_id="dead", status=RunStatus.RUNNING,
                     created_at=now - dt.timedelta(hours=2),
                     updated_at=now - dt.timedelta(hours=1))
    alive = RunRecord(run_id="alive", status=RunStatus.RUNNING, created_at=now, updated_at=now)
    done = RunRecord(run_id="done", status=RunStatus.DONE,
                     created_at=now - dt.timedelta(days=3),
                     updated_at=now - dt.timedelta(days=3))
    for rec in (dead, alive, done):
        runs.save_run(rec)

    with TestClient(create_app()) as started:                 # runs the lifespan hook
        assert _ok(started.get("/api/health")) == {"status": "ok"}

    assert runs.load_run("dead").status == RunStatus.INTERRUPTED
    assert "stopped responding" in runs.load_run("dead").error
    assert runs.load_run("alive").status == RunStatus.RUNNING     # still heartbeating
    assert runs.load_run("done").status == RunStatus.DONE


def test_startup_with_nothing_to_flag(bt_dirs):  # noqa: F811
    with TestClient(create_app()) as started:
        assert _ok(started.get("/api/health")) == {"status": "ok"}


# --- the error envelope ------------------------------------------------------------

def test_domain_errors_come_back_as_json_with_a_code(world):
    assert _ok(client.get("/api/cycles/current"), 404)["error"] == "no_active_cycle"
    assert _ok(client.delete("/api/cycles/current"), 404)["error"] == "no_active_cycle"
    assert _ok(client.get("/api/cycles/1999-01-01"), 404) == {
        "error": "cycle_not_found", "message": "No cycle 1999-01-01.", "cycle_id": "1999-01-01"}

    _ok(client.post("/api/cycles"), 201)
    conflict = _ok(client.post("/api/cycles"), 409)
    assert conflict["error"] == "cycle_already_open"
    assert _ok(client.post(f"{STEP}/part-b/advance"), 409)["error"] == "step_not_ready"
    assert _ok(client.post(f"{STEP}/nope/reopen"), 409)["error"] == "step_not_ready"


# --- one GREEN week, start to finish -------------------------------------------------

def test_a_green_week_through_every_step(world):
    created = _ok(client.post("/api/cycles", json={"week_end": "2026-11-13"}), 201)
    assert created["cycle_id"] == "2026-11-13"
    assert created["current_step"] == "part_a_regime_gate"
    assert [c["cycle_id"] for c in _ok(client.get("/api/cycles"))] == ["2026-11-13"]

    # Step 1 — regime gate
    ran, adv = _run_and_advance("part-a")
    assert ran["steps"]["part_a_regime_gate"]["output"]["regime"] == "GREEN"
    assert adv["current_step"] == "part_b_scanner"

    # Step 2 — scoring (frozen formula)
    ran, adv = _run_and_advance("part-b", {"universe_ids": ["holdings"]})
    scan = ran["steps"]["part_b_scanner"]["output"]
    assert scan["total"] == 5 and scan["eligible_count"] == 5
    assert adv["current_step"] == "part_c_exit_flags"

    # Step 3 — portfolio audit: both holdings rank inside 50, so nothing exits
    ran, adv = _run_and_advance("part-c")
    audit = ran["steps"]["part_c_exit_flags"]["output"]
    assert audit["exit_count"] == 0 and audit["keep_count"] == 2
    assert adv["current_step"] == "part_d_fundamental"

    # Step 4 — fundamental veto: holdings are excluded from the pool
    ran, adv = _run_and_advance("part-d-fundamental")
    fund = ran["steps"]["part_d_fundamental"]["output"]
    assert {c["symbol"] for c in fund["candidates"]} == {"ALPHA", "BETA", "GAMMA"}
    assert fund["struck_count"] == 0
    assert adv["current_step"] == "part_d_technical"

    # Step 5 — technical veto
    ran, adv = _run_and_advance("part-d-technical")
    tech = ran["steps"]["part_d_technical"]["output"]
    assert tech["passed_count"] + tech["struck_count"] == 3
    assert adv["current_step"] == "part_d_risk_caps"

    # Step 6 — risk caps
    ran, adv = _run_and_advance("part-d-risk-caps")
    caps = ran["steps"]["part_d_risk_caps"]["output"]
    assert [k["symbol"] for k in caps["kept"]] == ["AAA", "BBB"]
    assert [b["symbol"] for b in caps["buys"]] == tech["survivors"]
    assert caps["open_slots"] == 10
    assert adv["current_step"] == "part_e_journal"

    # Step 7 — journal: nothing is written until the step is advanced
    ran = _ok(client.post(f"{STEP}/part-e/run", json={"stops_verified": True, "notes": "ok"}))
    assert ran["steps"]["part_e_journal"]["output"]["committed"] is False
    assert _ok(client.get("/api/journal")) == []
    adv = _ok(client.post(f"{STEP}/part-e/advance"))
    assert adv["current_step"] == "phase3_plan"
    journal = _ok(client.get("/api/journal", params={"limit": 5}))
    assert len(journal) == 1 and journal[0]["cycle_id"] == "2026-11-13"
    assert journal[0]["regime"] == "GREEN" and journal[0]["stops_verified"] is True
    assert [b["symbol"] for b in journal[0]["buys"]] == [b["symbol"] for b in caps["buys"]]

    # Step 8 — Monday plan
    ran = _ok(client.post(f"{STEP}/phase3/run",
                          json={"portfolio_capital": 1_000_000, "available_cash": 500_000}))
    plan = ran["steps"]["phase3_plan"]["output"]
    assert plan["regime"] == "GREEN" and plan["exits"] == []
    assert plan["funded_count"] == len(caps["buys"])
    assert plan["checklist"][0].startswith("BEFORE THE OPEN")       # redeem from the fund
    done = _ok(client.post(f"{STEP}/phase3/advance"))
    assert done["current_step"] == "done" and done["outcome"] == "green_completed"

    # History: readable by id, reopenable, then closed
    assert _ok(client.get("/api/cycles/2026-11-13"))["outcome"] == "green_completed"
    reopened = _ok(client.post(f"{STEP}/part_d_risk_caps/reopen"))
    assert reopened["current_step"] == "part_d_risk_caps" and reopened["outcome"] is None
    assert reopened["steps"]["part_e_journal"]["output"] is None
    closed = _ok(client.post("/api/cycles/current/close"))
    assert closed["status"] == "closed"
    assert _ok(client.get("/api/cycles/current"), 404)["error"] == "no_active_cycle"
    assert len(_ok(client.get("/api/journal"))) == 1                 # closing keeps the record


def test_steps_run_with_no_request_body(world):
    _ok(client.post("/api/cycles"), 201)
    _run_and_advance("part-a")
    _run_and_advance("part-b")
    _run_and_advance("part-c")
    _run_and_advance("part-d-fundamental")
    _run_and_advance("part-d-technical")
    _run_and_advance("part-d-risk-caps")
    ran = _ok(client.post(f"{STEP}/part-e/run"))
    actions = ran["steps"]["part_e_journal"]["output"]["actions"]
    assert any("Stops not yet confirmed" in a for a in actions)
    _ok(client.post(f"{STEP}/part-e/advance"))
    ran = _ok(client.post(f"{STEP}/phase3/run"))
    assert ran["steps"]["phase3_plan"]["status"] == "done"


def test_deleting_a_cycle_removes_it_and_its_journal_row(world):
    created = _ok(client.post("/api/cycles"), 201)
    _run_and_advance("part-a")
    _run_and_advance("part-b")
    _run_and_advance("part-c")
    _run_and_advance("part-d-fundamental")
    _run_and_advance("part-d-technical")
    _run_and_advance("part-d-risk-caps")
    _run_and_advance("part-e")

    assert _ok(client.delete("/api/cycles/current")) == {
        "deleted": created["cycle_id"], "journal_entries_removed": 1}
    assert _ok(client.get("/api/cycles")) == [] and _ok(client.get("/api/journal")) == []


# --- the ledger ----------------------------------------------------------------------

def test_holdings_can_be_read_replaced_valued_and_looked_up(world):
    rows = _ok(client.get("/api/holdings"))
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]

    rows.append({"symbol": "alpha", "qty": 5, "entry_price": 50.0, "status": "ACTIVE"})
    saved = _ok(client.put("/api/holdings", json={"holdings": rows}))
    assert [r["symbol"] for r in saved] == ["AAA", "BBB", "ALPHA"]

    summary = _ok(client.get("/api/holdings/summary", params={"refresh": "true"}))
    assert summary["held_count"] == 3 and summary["unpriced"] == []
    assert summary["capital_deployed"] == pytest.approx(10 * 100 + 20 * 200 + 5 * 50)
    assert _ok(client.get("/api/holdings/summary"))["held_count"] == 3

    found = _ok(client.get("/api/holdings/lookup", params={"symbol": "alpha"}))
    assert (found["symbol"], found["found"], found["sector"]) == ("ALPHA", True, "Auto")
    assert found["sleeve"] == "MIDSMALLCAP400" and found["last_close"] > 0
