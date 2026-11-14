"""HOLDINGS_LEDGER — CSV-backed position ledger (the only ledger I/O layer).

Columns:
  symbol, qty, entry_price, entry_date, stop_price, sleeve, sector,
  status (ACTIVE | PENDING_EXIT | EXITED), exit_reason, marked_on
"""

from __future__ import annotations

import datetime as dt
import os
import shutil

import pandas as pd

from pathlib import Path as pathlib_Path

from .. import config

COLUMNS = [
    "symbol", "qty", "entry_price", "entry_date", "stop_price",
    "sleeve", "sector", "status", "exit_reason", "marked_on", "exit_price",
    "entry_charges", "exit_charges",
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


# Keep this many timestamped copies of the ledger. It is the one irreplaceable
# file in the app: cycles and prices regenerate, your real positions do not.
BACKUP_KEEP = 20


def _backup_dir():
    return config.HOLDINGS_LEDGER.parent / "ledger_backups"


def _snapshot(now: dt.datetime | None = None) -> None:
    """Copy the current ledger aside before overwriting it.

    Atomic writes protect against a HALF-written file; they do nothing about a
    fully-written WRONG one (a bad edit, an app bug, Excel reformatting the
    file). Backups are what make that recoverable, and no database would give
    it for free either.
    """
    src = config.HOLDINGS_LEDGER
    if not src.exists() or src.stat().st_size == 0:
        return
    d = _backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    # Millisecond precision, not seconds: two saves in the same second would
    # otherwise collide and the second would overwrite the first snapshot —
    # destroying exactly the copy you would want to roll back to.
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    shutil.copy2(src, d / f"holdings_ledger.{stamp}.csv")

    old = sorted(d.glob("holdings_ledger.*.csv"))[:-BACKUP_KEEP]
    for p in old:
        p.unlink()


def save_ledger(df: pd.DataFrame, now: dt.datetime | None = None) -> None:
    """Persist the ledger: snapshot the old one, then write atomically.

    The write goes to a temp file and is swapped in with os.replace, so a crash
    mid-write leaves the PREVIOUS ledger intact rather than a truncated one.
    This matches how cycles, the journal and backtest runs are already written —
    the ledger was the odd one out, despite being the most valuable file here.
    """
    if list(df.columns) != COLUMNS:
        raise ValueError(
            f"Refusing to write a ledger with unexpected columns: {list(df.columns)}")

    _snapshot(now)
    config.HOLDINGS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.HOLDINGS_LEDGER.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, config.HOLDINGS_LEDGER)


def list_backups() -> list[pathlib_Path]:
    """Newest first — what `restore_backup` can roll back to."""
    d = _backup_dir()
    return sorted(d.glob("holdings_ledger.*.csv"), reverse=True) if d.exists() else []


def restore_backup(name: str) -> bool:
    """Roll the ledger back to a named snapshot (itself snapshotted first)."""
    src = _backup_dir() / name
    if not src.exists():
        return False
    _snapshot()
    shutil.copy2(src, config.HOLDINGS_LEDGER)
    return True


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
            "exit_price": _num(r.get("exit_price"), float),
            "entry_charges": _num(r.get("entry_charges"), float),
            "exit_charges": _num(r.get("exit_charges"), float),
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
