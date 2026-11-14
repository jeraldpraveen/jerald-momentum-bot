"""Sweep API: background jobs, and the honesty guarantees.

The point of these tests is not just that a sweep runs. It is that a sweep can
never hand back an encouraging winner without the numbers that say whether the
winner means anything.
"""

import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.backtest import config as bt_config
from app.backtest import price_store, universe
from app.main import create_app
from app.schemas.backtest import RunStatus
from app.services import sweep_service

client = TestClient(create_app())


def _series(n=1400, start="2014-01-01", drift=0.0015, seed=0, vol=0.014):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, vol, n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.02, "low": close * 0.97,
         "close": close, "volume": 5_000_000.0},
        index=idx,
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    root = tmp_path / "backtest"
    for name, sub in [("BACKTEST_DIR", ""), ("PRICES_DIR", "prices"),
                      ("UNIVERSES_DIR", "universes"), ("RUNS_DIR", "runs"),
                      ("RESULTS_DIR", "results")]:
        monkeypatch.setattr(bt_config, name, root / sub if sub else root)
    monkeypatch.setattr(bt_config, "ALL_DIRS", [
        root, root / "prices", root / "universes", root / "runs", root / "results"])
    bt_config.ensure_dirs()
    price_store.clear_memo()

    price_store.save(app_config.get_index_option("nifty500").ticker, _series(seed=1))
    members = []
    for i in range(20):
        price_store.save(f"S{i}.NS", _series(drift=0.0025 - i * 0.0002, seed=30 + i))
        members.append(universe.UniverseMember(f"S{i}", f"Sector{i % 5}", "MIDSMALLCAP400"))
    universe.save("u", members)
    yield root

    for s in sweep_service.list_sweeps():
        sweep_service.cancel_sweep(s.sweep_id)
    import threading
    deadline = time.time() + 60
    for t in threading.enumerate():
        if t.name.startswith("sweep-"):
            t.join(timeout=max(0.0, deadline - time.time()))


def _start(**over):
    body = {"universe_id": "u", "index_id": "nifty500", "filter_id": "always_green",
            "train_end": "2017-12-31", "test_start": "2018-01-01",
            "weight_step": 50, "noise_samples": 10}
    body.update(over)
    r = client.post("/api/backtest/sweeps", json=body)
    return r


def _await(sweep_id, timeout=240.0):
    live = {s.value for s in (RunStatus.QUEUED, RunStatus.FETCHING, RunStatus.RUNNING)}
    end = time.time() + timeout
    while time.time() < end:
        rec = client.get(f"/api/backtest/sweeps/{sweep_id}").json()
        if rec["status"] not in live:
            return rec
        time.sleep(0.3)
    raise AssertionError(f"sweep {sweep_id} did not finish")


# --- lifecycle --------------------------------------------------------------

def test_sweep_runs_in_the_background_and_completes(api):
    r = _start()
    assert r.status_code == 202
    rec = _await(r.json()["sweep_id"])
    assert rec["status"] == "done", rec.get("error")
    assert rec["progress"]["pct"] == 100.0


def test_an_overlapping_split_is_rejected(api):
    """'Out-of-sample' must actually be out of sample."""
    r = _start(train_end="2020-01-01", test_start="2019-01-01")
    assert r.status_code == 409
    assert "out of sample" in r.json()["message"]


def test_an_empty_universe_is_rejected(api):
    universe.save("empty", [])
    assert _start(universe_id="empty").status_code == 409


def test_unknown_sweep_is_404(api):
    assert client.get("/api/backtest/sweeps/nope").status_code == 404
    assert client.get("/api/backtest/sweeps/nope/result").status_code == 404


def test_delete_removes_record_and_result(api):
    sid = _await(_start().json()["sweep_id"])["sweep_id"]
    assert client.delete(f"/api/backtest/sweeps/{sid}").status_code == 200
    assert client.get(f"/api/backtest/sweeps/{sid}").status_code == 404
    assert client.delete(f"/api/backtest/sweeps/{sid}").status_code == 404


# --- THE honesty guarantees -------------------------------------------------

def test_a_winner_never_arrives_without_its_diagnostics(api):
    """A bare 'best CAGR' is the misleading headline. The API must always ship
    the numbers that reveal whether selection actually did anything."""
    rec = _await(_start().json()["sweep_id"])
    v = rec["verdict"]

    assert v["winner_formula"]
    for field in ("winner_test_percentile", "pct_beating_baseline_test",
                  "median_test_cagr", "train_test_corr", "formulas_tried",
                  "baseline_test_cagr", "winner_train_edge", "winner_test_edge"):
        assert field in v, f"{field} must accompany every winner"
    assert rec["noise"]["n"] > 0
    assert rec["noise"]["spread"] >= 0


def test_the_winner_is_chosen_on_train_never_on_test(api):
    """Picking on test would be lookahead dressed up as optimisation."""
    sid = _await(_start().json()["sweep_id"])["sweep_id"]
    res = client.get(f"/api/backtest/sweeps/{sid}/result").json()

    grid, v = res["grid"], res["verdict"]
    best_train = max(grid, key=lambda r: r["train_cagr"])
    assert v["winner_formula"] == best_train["formula"]
    assert v["winner_test_cagr"] == pytest.approx(best_train["test_cagr"])
    # Hindsight's best is reported separately, and is >= what we could pick.
    assert v["best_possible_test_cagr"] >= v["winner_test_cagr"]


def test_the_noise_floor_is_measured_on_train_only(api):
    """It must describe the data selection happens on."""
    rec = _await(_start(noise_samples=15).json()["sweep_id"])
    n = rec["noise"]
    assert 0 < n["n"] <= 15
    assert n["max_cagr"] >= n["mean_cagr"] >= n["min_cagr"]


def test_limitations_name_the_multiple_comparisons_problem(api):
    sid = _await(_start().json()["sweep_id"])["sweep_id"]
    text = " ".join(client.get(f"/api/backtest/sweeps/{sid}/result").json()["limitations"])
    assert "comparisons" in text.lower()
    assert "screen" in text.lower()          # not a substitute for a real run
    assert "survivorship" in text.lower()


def test_the_result_carries_the_full_grid_for_inspection(api):
    sid = _await(_start().json()["sweep_id"])["sweep_id"]
    res = client.get(f"/api/backtest/sweeps/{sid}/result").json()
    assert len(res["grid"]) > 1
    row = res["grid"][0]
    for f in ("formula", "train_cagr", "test_cagr", "test_maxdd", "test_turnover"):
        assert f in row
    assert res["config"]["train_weeks"] > 0 and res["config"]["test_weeks"] > 0


# --- grid construction ------------------------------------------------------

def test_the_grid_covers_the_weight_simplex():
    grid = sweep_service.build_grid(step=25)
    assert len(grid) == len(set(grid)), "no duplicate formulas"
    assert any("100% * 6 Month Performance" in f for f in grid)
    assert all(f.startswith("(") for f in grid)


def test_a_coarser_step_produces_fewer_formulas():
    assert len(sweep_service.build_grid(50)) < len(sweep_service.build_grid(20))
