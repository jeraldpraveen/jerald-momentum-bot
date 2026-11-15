"""Part B routes — the frozen formula over HTTP (Spec v2.3)."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.domain import scoring_formula
from app.main import app
from app.services import scanner_service
from tests.unit.test_scanner_service import HOLDINGS, _ohlcv, at_part_b  # noqa: F401

client = TestClient(app)


def _prices(monkeypatch):
    monkeypatch.setattr(
        scanner_service.stock_prices, "get_prices",
        lambda tickers, as_of=None, today=None: {t: _ohlcv(np.linspace(100, 300, 400))
                                                 for t in tickers})


# --- the frozen formula --------------------------------------------------------

def test_frozen_formula_endpoint_returns_the_live_formula():
    body = client.get("/api/scanner/frozen-formula").json()

    assert body["formula"] == scoring_formula.FROZEN_FORMULA
    assert body["since"] == "Spec v2.3"
    assert "Backtest tab" in body["reason"]


def test_running_part_b_with_another_formula_is_a_409(at_part_b):  # noqa: F811
    r = client.post("/api/cycles/current/steps/part-b/run",
                    json={"universe_ids": ["holdings"],
                          "formula": "6 Month Performance / 1 Month Volatility"})

    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "formula_frozen"
    assert body["frozen_formula"] == scoring_formula.FROZEN_FORMULA
    assert "frozen (Spec v2.3)" in body["message"]


def _universe(monkeypatch):
    """With no universe named, the scan covers the two Rulebook indices — faked
    here so the test does not reach out to NSE."""
    asked = []
    monkeypatch.setattr(
        scanner_service, "_resolve_universes",
        lambda ids: asked.append(list(ids)) or [(f"{s}.NS", sec, "") for s, sec in HOLDINGS])
    return asked


def test_running_part_b_with_no_body_scores_the_frozen_formula(at_part_b, monkeypatch):  # noqa: F811
    _prices(monkeypatch)
    asked = _universe(monkeypatch)
    r = client.post("/api/cycles/current/steps/part-b/run")

    assert r.status_code == 200
    step = r.json()["steps"]["part_b_scanner"]
    assert step["status"] == "done"
    assert step["output"]["formula"] == scoring_formula.FROZEN_FORMULA
    assert asked == [["midsmall400", "microcap250"]]            # not the holdings alone
    assert step["output"]["universe_ids"] == ["midsmall400", "microcap250"]


def test_part_b_advances_after_a_run(at_part_b, monkeypatch):  # noqa: F811
    _prices(monkeypatch)
    client.post("/api/cycles/current/steps/part-b/run", json={"universe_ids": ["holdings"]})
    r = client.post("/api/cycles/current/steps/part-b/advance")

    assert r.status_code == 200
    assert r.json()["current_step"] == "part_c_exit_flags"


# --- formula validation (still used for research formulas) ---------------------

def test_validating_the_frozen_formula_says_it_is_frozen():
    for text in ("", scoring_formula.FROZEN_FORMULA):
        body = client.post("/api/scanner/validate-formula", json={"formula": text}).json()
        assert body["ok"] is True and body["frozen"] is True
        assert body["terms"] == ["perf_1m", "perf_3m", "perf_6m", "vol_1m", "vol_3m"]


def test_validating_a_different_valid_formula_is_ok_but_not_frozen():
    body = client.post("/api/scanner/validate-formula",
                       json={"formula": "6 Month Performance / 1 Month Volatility"}).json()
    assert body == {"ok": True, "error": None, "terms": ["perf_6m", "vol_1m"],
                    "frozen": False}


def test_validating_a_broken_formula_reports_the_error():
    body = client.post("/api/scanner/validate-formula",
                       json={"formula": "momentum * 6 Month Performance"}).json()
    assert body["ok"] is False and body["frozen"] is False
    assert "Unknown term" in body["error"]


# --- universe helpers ----------------------------------------------------------

def test_universe_options_lists_the_selectable_universes():
    ids = [o["id"] for o in client.get("/api/universe-options").json()]
    assert ids[0] == "holdings" and "midsmall400" in ids


def test_universe_sectors_are_the_union_for_the_selection(at_part_b, monkeypatch):  # noqa: F811
    expected = sorted({sec for _, sec in HOLDINGS})
    assert client.get("/api/universe-sectors", params={"ids": "holdings"}).json() == expected

    asked = _universe(monkeypatch)
    assert client.get("/api/universe-sectors").json() == expected
    assert asked == [["midsmall400", "microcap250"]]            # the default selection
