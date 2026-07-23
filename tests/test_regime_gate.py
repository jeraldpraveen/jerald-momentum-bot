"""Integration tests for Part A branches, isolated from the real data/ dir."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from momentum_bot import config, regime_gate
from momentum_bot.broker import DryRunBroker
from momentum_bot.data_fetch import IndexData


def _weekly(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes},
        index=pd.date_range("2024-01-05", periods=len(closes), freq="W-FRI"),
    )


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "HOLDINGS_LEDGER", tmp_path / "holdings_ledger.csv")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(config, "JOURNAL_MD", tmp_path / "journal.md")
    monkeypatch.setattr(config, "JOURNAL_JSONL", tmp_path / "journal.jsonl")
    pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"], "qty": [10, 20], "entry_price": [100, 200],
            "entry_date": ["2026-06-01", "2026-06-08"], "stop_price": [80, 160],
            "sleeve": ["MIDSMALLCAP400"] * 2, "sector": ["X", "Y"],
            "status": ["ACTIVE", "ACTIVE"], "exit_reason": ["", ""], "marked_on": ["", ""],
        }
    ).to_csv(tmp_path / "holdings_ledger.csv", index=False)
    return tmp_path


def _patch_fetch(monkeypatch, closes):
    monkeypatch.setattr(
        regime_gate,
        "fetch_weekly_index_data",
        lambda now_ist: IndexData(weekly=_weekly(closes), source="test"),
    )


def test_red_branch_marks_holdings_and_cancels_gtts(isolated_env, monkeypatch):
    _patch_fetch(monkeypatch, list(np.linspace(100, 200, 40)) + [150, 140, 130])
    broker = DryRunBroker()
    entry = regime_gate.run_regime_gate(broker=broker, now_ist=dt.datetime.now(config.IST))

    assert entry["regime"] == "RED"
    assert entry["pending_exit"] == ["AAA", "BBB"]
    assert entry["active_holdings"] == []
    assert any("CANCEL_ALL_GTTS" in a for a in broker.actions)

    saved = pd.read_csv(isolated_env / "holdings_ledger.csv")
    assert (saved["status"] == "PENDING_EXIT").all()
    assert (saved["exit_reason"] == "REGIME_RED").all()
    assert (isolated_env / "journal.jsonl").exists()
    assert (isolated_env / "journal.md").exists()


def test_green_branch_leaves_ledger_untouched(isolated_env, monkeypatch):
    _patch_fetch(monkeypatch, np.linspace(100, 200, 40))
    entry = regime_gate.run_regime_gate(now_ist=dt.datetime.now(config.IST))

    assert entry["regime"] == "GREEN"
    assert entry["reentry_required"] is False
    saved = pd.read_csv(isolated_env / "holdings_ledger.csv")
    assert (saved["status"] == "ACTIVE").all()


def test_red_to_green_flags_mandatory_reentry(isolated_env, monkeypatch):
    _patch_fetch(monkeypatch, list(np.linspace(100, 200, 40)) + [150, 140, 130])
    regime_gate.run_regime_gate(now_ist=dt.datetime.now(config.IST))  # week 1: RED

    _patch_fetch(
        monkeypatch,
        list(np.linspace(100, 200, 40)) + [150, 140, 130] + list(np.linspace(140, 230, 15)),
    )
    entry = regime_gate.run_regime_gate(now_ist=dt.datetime.now(config.IST))  # week 2: GREEN

    assert entry["regime"] == "GREEN"
    assert entry["reentry_required"] is True
    assert any("mandatory re-entry" in a for a in entry["actions"])
