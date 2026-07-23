"""Backtest universes — a customizable CSV of tickers the run may trade.

A universe file is `data/backtest/universes/<id>.csv` with columns:
    symbol, sector, sleeve
`sleeve` is MIDSMALLCAP400 or MICROCAP250 and drives the microcap cap.

**Survivorship bias, stated plainly:** a universe is a FIXED list — today's
index membership applied to all of history. Names that were in the index years
ago and have since been delisted or demoted are absent, so results are
optimistic. Point-in-time membership is the fix and it is not freely available;
until then this limitation is reported with every run rather than hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .. import config as app_config
from ..integrations import constituents
from . import config as bt_config

log = logging.getLogger(__name__)

COLUMNS = ["symbol", "sector", "sleeve"]


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    sector: str = ""
    sleeve: str = "MIDSMALLCAP400"

    @property
    def ticker(self) -> str:
        return f"{self.symbol}.NS"


def _path(universe_id: str):
    return bt_config.UNIVERSES_DIR / f"{universe_id}.csv"


def list_universes() -> list[str]:
    if not bt_config.UNIVERSES_DIR.exists():
        return []
    return sorted(p.stem for p in bt_config.UNIVERSES_DIR.glob("*.csv"))


def load(universe_id: str) -> list[UniverseMember]:
    p = _path(universe_id)
    if not p.exists():
        raise FileNotFoundError(
            f"No backtest universe {universe_id!r}. Build one from an NSE index first.")
    df = pd.read_csv(p)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    members: list[UniverseMember] = []
    for r in df.itertuples():
        # A blank cell reads back as NaN; str(NaN) is "nan", which would sail
        # through a truthiness check and become the ticker "NAN.NS".
        if pd.isna(r.symbol) or not str(r.symbol).strip():
            continue
        members.append(UniverseMember(
            symbol=str(r.symbol).strip().upper(),
            sector="" if pd.isna(r.sector) else str(r.sector),
            sleeve=(str(r.sleeve).strip()
                    if pd.notna(r.sleeve) and str(r.sleeve).strip() else "MIDSMALLCAP400"),
        ))
    return members


def save(universe_id: str, members: list[UniverseMember]) -> int:
    bt_config.ensure_dirs()
    # `columns=` matters: an empty list would otherwise write a zero-byte file
    # that pandas cannot read back ("No columns to parse").
    pd.DataFrame(
        [{"symbol": m.symbol, "sector": m.sector, "sleeve": m.sleeve} for m in members],
        columns=COLUMNS,
    ).to_csv(_path(universe_id), index=False)
    return len(members)


# Which live universe ids map to which sleeve for the microcap cap.
_SLEEVE_BY_ID = {"microcap250": "MICROCAP250"}


def build_from_indices(universe_id: str, index_ids: list[str]) -> list[UniverseMember]:
    """Snapshot today's constituents of one or more NSE indices into a file.

    This is a SNAPSHOT taken now — see the survivorship note in this module's
    docstring. Deduped by symbol; the first index listing a name wins its sleeve.
    """
    seen: dict[str, UniverseMember] = {}
    for uid in index_ids:
        opt = app_config.get_universe_option(uid)
        if opt.source != "nse_csv":
            log.info("Skipping universe %s (source %s is not a constituent list)", uid, opt.source)
            continue
        df = constituents.fetch_constituents(opt.nse_key)
        sleeve = _SLEEVE_BY_ID.get(uid, "MIDSMALLCAP400")
        for r in df.itertuples():
            sym = str(r.symbol).strip().upper()
            if sym:
                seen.setdefault(sym, UniverseMember(sym, str(r.sector), sleeve))

    members = sorted(seen.values(), key=lambda m: m.symbol)
    save(universe_id, members)
    return members


def microcap_symbols(members: list[UniverseMember]) -> set[str]:
    """The set the microcap cap is evaluated against, straight from the file."""
    return {m.symbol for m in members if m.sleeve == "MICROCAP250"}
