"""HOLDINGS_LEDGER — CSV-backed position ledger.

Columns:
  symbol, qty, entry_price, entry_date, stop_price, sleeve, sector,
  status (ACTIVE | PENDING_EXIT | EXITED), exit_reason, marked_on
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import config

COLUMNS = [
    "symbol", "qty", "entry_price", "entry_date", "stop_price",
    "sleeve", "sector", "status", "exit_reason", "marked_on",
]
TEXT_COLUMNS = ["symbol", "entry_date", "sleeve", "sector", "status", "exit_reason", "marked_on"]


def load_ledger() -> pd.DataFrame:
    if not config.HOLDINGS_LEDGER.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(config.HOLDINGS_LEDGER)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    for c in TEXT_COLUMNS:
        # Empty CSV columns read back as float NaN; keep text columns as strings.
        df[c] = df[c].fillna("").astype(str)
    return df[COLUMNS]


def save_ledger(df: pd.DataFrame) -> None:
    config.HOLDINGS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.HOLDINGS_LEDGER, index=False)


def mark_all_for_liquidation(df: pd.DataFrame, reason: str, as_of: dt.date) -> tuple[pd.DataFrame, list[str]]:
    """RED regime: flag every ACTIVE row for sale at Monday open. Returns (ledger, symbols marked)."""
    df = df.copy()
    active = df["status"] == "ACTIVE"
    df.loc[active, "status"] = "PENDING_EXIT"
    df.loc[active, "exit_reason"] = reason
    df.loc[active, "marked_on"] = as_of.isoformat()
    return df, df.loc[df["exit_reason"] == reason, "symbol"].tolist()
