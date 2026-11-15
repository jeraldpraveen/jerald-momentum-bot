"""Backtest API — Spec v2.3 additions: benchmarks, after-tax runs, parked cash."""

import threading

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import benchmarks, price_store, runs
from app.schemas.backtest import BacktestRunRequest, RunStatus
from app.services import backtest_service
from tests.integration.test_backtest_api import _await_run, _series, _start, api, client  # noqa: F401


def _tri_file(root, name="midsmall400_tri.csv"):
    d = root / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    idx = pd.bdate_range("2020-01-01", periods=900)
    pd.DataFrame({"Date": idx.strftime("%d %b %Y"),
                  "Total Returns Index": 100 * np.cumprod(np.full(900, 1.0005))}
                 ).to_csv(d / name, index=False)


# --- request defaults ---------------------------------------------------------

def test_a_run_request_defaults_to_the_v23_rules():
    body = BacktestRunRequest(universe_id="u")
    assert body.index_id == "nifty500"            # the gate that was actually tested
    assert body.cash_yield_pct == 6.5             # idle cash parked in a liquid fund
    assert body.apply_tax is True
    assert body.benchmark_id is None


# --- GET /benchmarks ----------------------------------------------------------

def test_benchmarks_lists_missing_total_return_files_with_instructions(api):  # noqa: F811
    rows = {r["id"]: r for r in client.get("/api/backtest/benchmarks").json()}

    tri = rows["midsmall400_tri"]
    assert tri["available"] is False and tri["total_return"] is True
    assert tri["source"] == "manual" and tri["first_session"] is None
    assert "niftyindices.com" in tri["how_to"] and "midsmall400_tri.csv" in tri["how_to"]


def test_benchmarks_reports_coverage_for_what_is_on_disk(api):  # noqa: F811
    _tri_file(api)
    rows = {r["id"]: r for r in client.get("/api/backtest/benchmarks").json()}

    tri = rows["midsmall400_tri"]
    assert tri["available"] is True and tri["how_to"] == ""
    assert tri["first_session"] == "2020-01-01" and tri["last_session"] > "2023-01-01"

    cached = rows[app_config.DEFAULT_INDEX_ID]          # the fixture cached this index
    assert cached["available"] is True and cached["total_return"] is False

    uncached = rows["banknifty"]
    assert uncached["available"] is False and uncached["how_to"] == ""


# --- after-tax runs -----------------------------------------------------------

def test_a_default_run_is_after_tax_end_to_end(api):  # noqa: F811
    rec = _await_run(_start()["run_id"])

    assert rec["status"] == "done", rec.get("error")
    assert rec["request"]["apply_tax"] is True
    assert rec["request"]["cash_yield_pct"] == 6.5
    assert rec["summary"]["tax"]["enabled"] is True
    assert rec["summary"]["config"]["apply_tax"] is True

    result = client.get(f"/api/backtest/runs/{rec['run_id']}/result").json()
    assert result["tax"]["enabled"] is True
    assert {"strategy", "benchmark", "by_year", "rules"} <= set(result["tax"])


def test_a_pre_tax_run_is_marked_as_such(api):  # noqa: F811
    rec = _await_run(_start(apply_tax=False)["run_id"])

    assert rec["status"] == "done", rec.get("error")
    assert rec["summary"]["tax"] == {"enabled": False}
    result = client.get(f"/api/backtest/runs/{rec['run_id']}/result").json()
    assert any("Tax is NOT modelled" in l for l in result["limitations"])


# --- benchmark choice ---------------------------------------------------------

def test_a_run_against_the_total_return_file(api):  # noqa: F811
    _tri_file(api)
    rec = _await_run(_start(benchmark_id="midsmall400_tri")["run_id"])

    assert rec["status"] == "done", rec.get("error")
    cfg = rec["summary"]["config"]
    assert cfg["benchmark_label"] == "NIFTY MIDSMALLCAP 400 TRI"
    assert cfg["benchmark_total_return"] is True


def test_a_missing_benchmark_file_fails_the_run_with_instructions(api):  # noqa: F811
    rec = _await_run(_start(benchmark_id="midsmall400_tri")["run_id"])

    assert rec["status"] == "failed"
    assert "niftyindices.com" in rec["error"]


# --- price fetching -----------------------------------------------------------

@pytest.mark.parametrize("benchmark_id,expected", [
    (None, None),
    ("", None),
    ("midsmall400_tri", None),            # a downloaded file: nothing to fetch
    ("midsmall400", "MIDSMALL.NS"),
    ("nope", None),                       # the engine rejects it later
])
def test_benchmark_ticker_to_fetch(benchmark_id, expected):
    assert backtest_service._benchmark_ticker(benchmark_id) == expected


def test_fetching_prices_includes_the_benchmark_once(api, monkeypatch):  # noqa: F811
    seen = {}

    def fake_ensure(tickers, *, batch, progress):
        seen["tickers"] = list(tickers)
        price_store.save("MIDSMALL.NS", _series(seed=7))
        progress(len(tickers), len(tickers))
        return {t: True for t in tickers}

    monkeypatch.setattr(price_store, "ensure", fake_ensure)
    rec = _await_run(_start(fetch_prices=True, benchmark_id="midsmall400")["run_id"])

    assert rec["status"] == "done", rec.get("error")
    index = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    assert seen["tickers"].count("MIDSMALL.NS") == 1
    assert seen["tickers"][0] == index and seen["tickers"].count(index) == 1
    assert rec["summary"]["config"]["benchmark_id"] == "midsmall400"


def test_the_regime_index_is_not_fetched_twice_as_its_own_benchmark(api, monkeypatch):  # noqa: F811
    seen = {}
    monkeypatch.setattr(price_store, "ensure",
                        lambda tickers, *, batch, progress: seen.update(t=list(tickers)))
    rec = _await_run(_start(fetch_prices=True,
                            benchmark_id=app_config.DEFAULT_INDEX_ID)["run_id"])

    assert rec["status"] == "done", rec.get("error")
    index = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    assert seen["t"].count(index) == 1


def test_fetch_also_pulls_the_parking_asset(api, monkeypatch):  # noqa: F811
    seen = {}

    def fake_ensure(tickers, *, batch, progress):
        seen["t"] = list(tickers)
        price_store.save("GOLDBEES.NS", _series(seed=9))

    monkeypatch.setattr(price_store, "ensure", fake_ensure)
    rec = _await_run(_start(fetch_prices=True, alt_asset_id="gold")["run_id"])

    assert rec["status"] == "done", rec.get("error")
    assert "GOLDBEES.NS" in seen["t"]


def test_cancelling_during_the_fetch_stops_before_the_weeks(api, monkeypatch):  # noqa: F811
    def fake_ensure(tickers, *, batch, progress):
        for run in backtest_service.list_runs():
            backtest_service.cancel_run(run.run_id)

    monkeypatch.setattr(price_store, "ensure", fake_ensure)
    rec = _await_run(_start(fetch_prices=True)["run_id"])

    assert rec["status"] == "cancelled" and rec["error"] == "Cancelled."


def test_cancelling_mid_run_stops_at_the_next_checkpoint(api, monkeypatch):  # noqa: F811
    from app.backtest import engine
    real = engine.run_backtest

    def cancel_then_run(**kw):
        for run in backtest_service.list_runs():
            assert backtest_service.cancel_run(run.run_id) is True
        return real(**kw)

    monkeypatch.setattr(engine, "run_backtest", cancel_then_run)
    rec = _await_run(_start()["run_id"])
    assert rec["status"] == "cancelled"


def test_cancelling_an_unknown_run_is_false(api):  # noqa: F811
    assert backtest_service.cancel_run("nope") is False
    assert client.post("/api/backtest/runs/nope/cancel").json() == {"cancelling": False}


def test_the_worker_ignores_a_run_whose_record_is_gone(api):  # noqa: F811
    backtest_service._execute("ghost", BacktestRunRequest(universe_id="testu"),
                              threading.Event())
    assert runs.load_run("ghost") is None


# --- read-only option endpoints ------------------------------------------------

def test_alt_assets_mark_gold_as_research_only(api):  # noqa: F811
    rows = {r["id"]: r for r in client.get("/api/backtest/alt-assets").json()}
    assert "RESEARCH ONLY" in rows["gold"]["note"]
    assert "not adopted" in rows["gold_sbi"]["note"]
    assert "liquid" in rows


def test_index_coverage_reports_cached_and_uncached_indices(api):  # noqa: F811
    rows = {r["id"]: r for r in client.get("/api/backtest/index-coverage").json()}

    cached = rows[app_config.DEFAULT_INDEX_ID]
    assert cached["cached"] is True and cached["first_session"] == "2020-01-01"
    assert cached["first_tradable"] > cached["first_session"]
    assert rows["banknifty"] == {
        "id": "banknifty", "label": "NIFTY BANK", "ticker": "^NSEBANK",
        "first_session": None, "last_session": None, "first_tradable": None,
        "cached": False,
    }


def test_sweep_listing_and_cancel_endpoints_respond(api):  # noqa: F811
    assert client.get("/api/backtest/sweeps").json() == []
    assert client.post("/api/backtest/sweeps/nope/cancel").json() == {"cancelling": False}


def test_a_run_failure_lands_on_the_record(api):  # noqa: F811
    rec = _await_run(_start(benchmark_id="nope")["run_id"])
    assert rec["status"] == RunStatus.FAILED.value
    assert "Unknown benchmark" in rec["error"]
    assert benchmarks.BenchmarkError.__name__ not in rec["error"]   # surfaced as BacktestError
