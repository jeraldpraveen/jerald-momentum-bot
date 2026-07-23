"""HOLDINGS_LEDGER — CSV-backed position ledger (the only ledger I/O layer).

Columns:
  symbol, qty, entry_price, entry_date, stop_price, sleeve, sector,
  status (ACTIVE | PENDING_EXIT | EXITED), exit_reason, marked_on
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .. import config

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
        # Empty CSV cells read back as float NaN; keep text columns as strings.
        df[c] = df[c].fillna("").astype(str)
    return df[COLUMNS]


def save_ledger(df: pd.DataFrame) -> None:
    config.HOLDINGS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.HOLDINGS_LEDGER, index=False)


def mark_all_for_liquidation(
    df: pd.DataFrame, reason: str, as_of: dt.date
) -> tuple[pd.DataFrame, list[str]]:
    """RED regime: flag every ACTIVE row for sale at Monday open.

    Returns (updated ledger, symbols newly marked).
    """
    df = df.copy()
    active = df["status"] == "ACTIVE"
    df.loc[active, "status"] = "PENDING_EXIT"
    df.loc[active, "exit_reason"] = reason
    df.loc[active, "marked_on"] = as_of.isoformat()
    return df, df.loc[df["exit_reason"] == reason, "symbol"].tolist()


def _num(v, cast):
    """CSV blanks / NaN -> None; otherwise cast."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    try:
        return cast(float(v))
    except (TypeError, ValueError):
        return None


def to_holdings(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> plain dicts matching the Holding schema."""
    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        rows.append({
            "symbol": str(r.get("symbol", "")).strip(),
            "qty": _num(r.get("qty"), int),
            "entry_price": _num(r.get("entry_price"), float),
            "entry_date": "" if pd.isna(r.get("entry_date")) else str(r.get("entry_date") or "").strip(),
            "stop_price": _num(r.get("stop_price"), float),
            "sleeve": str(r.get("sleeve") or "").strip(),
            "sector": str(r.get("sector") or "").strip(),
            "status": (str(r.get("status") or "ACTIVE").strip() or "ACTIVE"),
            "exit_reason": str(r.get("exit_reason") or "").strip(),
            "marked_on": str(r.get("marked_on") or "").strip(),
        })
    return [r for r in rows if r["symbol"]]


def from_holdings(holdings: list[dict]) -> pd.DataFrame:
    """Typed holdings -> the ledger DataFrame shape (blanks for None)."""
    df = pd.DataFrame([{c: h.get(c) for c in COLUMNS} for h in holdings], columns=COLUMNS)
    for c in TEXT_COLUMNS:
        df[c] = df[c].fillna("").astype(str)
    return df


def active_symbols(df: pd.DataFrame) -> list[str]:
    return df.loc[df["status"] == "ACTIVE", "symbol"].tolist()


def pending_exit_symbols(df: pd.DataFrame) -> list[str]:
    return df.loc[df["status"] == "PENDING_EXIT", "symbol"].tolist()
