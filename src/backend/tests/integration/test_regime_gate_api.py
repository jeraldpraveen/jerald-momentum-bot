"""End-to-end API slice: create cycle -> run Part A -> advance, plus 409 guard."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import regime_gate_service
from tests.conftest import green_data

client = TestClient(app)


@pytest.fixture
def api(isolated, monkeypatch):
    """Isolate paths and stub market data so the API test is deterministic."""
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    return isolated


def test_regime_options_lists_indices_and_filters(api):
    r = client.get("/api/regime-options")
    assert r.status_code == 200
    body = r.json()
    ids = [o["id"] for o in body["indices"]]
    assert "midsmall400" in ids and "nifty50" in ids
    assert body["default_index_id"] == "nifty500"         # the ratified gate (v2.3)
    assert any(f["id"] == "always_green" for f in body["filters"])


def test_run_with_selection_echoes_labels(api):
    client.post("/api/cycles", json={"week_end": "2026-07-17"})
    r = client.post(
        "/api/cycles/current/steps/part-a/run",
        json={"index_id": "nifty50", "filter_id": "st_1_2"},
    )
    out = r.json()["steps"]["part_a_regime_gate"]["output"]
    assert out["index_label"] == "NIFTY 50"
    assert out["filter_label"] == "Supertrend (1, 2)"


def test_no_active_cycle_returns_404(api):
    r = client.get("/api/cycles/current")
    assert r.status_code == 404
    assert r.json()["error"] == "no_active_cycle"


def test_full_part_a_flow(api):
    # Start a cycle.
    r = client.post("/api/cycles", json={"week_end": "2026-07-17"})
    assert r.status_code == 201
    assert r.json()["current_step"] == "part_a_regime_gate"

    # Second cycle while one is open -> 409.
    r = client.post("/api/cycles", json={"week_end": "2026-07-24"})
    assert r.status_code == 409
    assert r.json()["error"] == "cycle_already_open"

    # Run Part A.
    r = client.post("/api/cycles/current/steps/part-a/run")
    assert r.status_code == 200
    step = r.json()["steps"]["part_a_regime_gate"]
    assert step["status"] == "done"
    assert step["output"]["regime"] == "GREEN"

    # Advance (GREEN -> Part B).
    r = client.post("/api/cycles/current/steps/part-a/advance")
    assert r.status_code == 200
    assert r.json()["current_step"] == "part_b_scanner"


def test_advance_before_run_returns_409(api):
    client.post("/api/cycles", json={"week_end": "2026-07-17"})
    r = client.post("/api/cycles/current/steps/part-a/advance")
    assert r.status_code == 409
    assert r.json()["error"] == "step_not_ready"


def test_last_step_rejects_out_of_order_run(api):
    """Every step is implemented now — running one early is a gate error, not a 501."""
    client.post("/api/cycles", json={"week_end": "2026-07-17"})
    r = client.post("/api/cycles/current/steps/phase3/run")
    assert r.status_code == 409
    assert r.json()["error"] == "step_not_ready"
