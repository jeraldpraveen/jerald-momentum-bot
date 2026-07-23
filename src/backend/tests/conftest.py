"""Shared fixtures — isolate config paths to a tmp dir and seed a ledger."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app import config
from app.integrations.market_data import WeeklyIndexData


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point every persistence path at a throwaway dir + seed 2 ACTIVE holdings."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CYCLES_DIR", tmp_path / "cycles")
    monkeypatch.setattr(config, "CURRENT_CYCLE_FILE", tmp_path / "_current_cycle.json")
    monkeypatch.setattr(config, "HOLDINGS_LEDGER", tmp_path / "holdings_ledger.csv")
    monkeypatch.setattr(config, "MANUAL_DIR", tmp_path / "index_manual")

    pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"], "qty": [10, 20], "entry_price": [100, 200],
            "entry_date": ["2026-06-01", "2026-06-08"], "stop_price": [80, 160],
            "sleeve": ["MIDSMALLCAP400", "MIDSMALLCAP400"], "sector": ["X", "Y"],
            "status": ["ACTIVE", "ACTIVE"], "exit_reason": ["", ""], "marked_on": ["", ""],
        }
    ).to_csv(tmp_path / "holdings_ledger.csv", index=False)
    return tmp_path


def make_weekly(closes, spread_pct=0.01):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * (1 + spread_pct),
            "low": closes * (1 - spread_pct),
            "close": closes,
        },
        index=pd.date_range("2024-01-05", periods=len(closes), freq="W-FRI"),
    )


def green_data():
    return WeeklyIndexData(weekly=make_weekly(np.linspace(100, 200, 40)), source="test")


def red_data():
    closes = list(np.linspace(100, 200, 40)) + [150, 140, 130]
    return WeeklyIndexData(weekly=make_weekly(closes), source="test")


def recovery_data():
    closes = list(np.linspace(100, 200, 40)) + [150, 140, 130] + list(np.linspace(140, 230, 15))
    return WeeklyIndexData(weekly=make_weekly(closes), source="test")


@pytest.fixture
def now_ist():
    return dt.datetime(2026, 7, 20, 16, 0, tzinfo=config.IST)
