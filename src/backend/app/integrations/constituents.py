"""Fetch index constituents (stock list + sector) from NSE, cached to disk.

Unlike NSE's price APIs (bot-blocked), the constituent CSVs at
archives.nseindia.com are plain, fetchable text/csv. Columns:
    Company Name, Industry, Symbol, Series, ISIN Code
We keep Symbol (-> <Symbol>.NS for yfinance) and Industry (-> sector for caps).

Caveat: this is the CURRENT membership only (survivorship bias for history).
"""

from __future__ import annotations

import logging

import pandas as pd
import requests

from .. import config

log = logging.getLogger(__name__)

_ARCHIVE = "https://archives.nseindia.com/content/indices/{key}.csv"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


def _cache_path(nse_key: str):
    return config.UNIVERSES_DIR / f"{nse_key}.csv"


def fetch_constituents(nse_key: str, force_refresh: bool = False) -> pd.DataFrame:
    """Return DataFrame[symbol, sector, company] for an NSE index; cache locally."""
    cache = _cache_path(nse_key)
    if cache.exists() and not force_refresh:
        return pd.read_csv(cache)

    url = _ARCHIVE.format(key=nse_key)
    r = requests.get(url, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    from io import StringIO

    raw = pd.read_csv(StringIO(r.text))
    df = pd.DataFrame({
        "symbol": raw["Symbol"].astype(str).str.strip(),
        "sector": raw["Industry"].astype(str).str.strip(),
        "company": raw["Company Name"].astype(str).str.strip(),
    })
    config.UNIVERSES_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    log.info("Fetched %d constituents for %s", len(df), nse_key)
    return df
