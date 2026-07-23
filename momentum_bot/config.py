"""Frozen strategy parameters (Rulebook v1.1 — no changes until Jan 2027 review)."""

from pathlib import Path
from zoneinfo import ZoneInfo

# --- Regime gate -----------------------------------------------------------
INDEX_NAME = "NIFTY MIDSMALLCAP 400"
SUPERTREND_ATR_PERIOD = 1
SUPERTREND_MULTIPLIER = 2.5

# Yahoo Finance proxy: Mirae Asset Nifty MidSmallcap 400 ETF (tracks INDEX_NAME).
# Used only when the official NSE index history is unreachable.
YF_PROXY_TICKER = "MIDSMALL.NS"

IST = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# Weekly bars required for the Supertrend recursion to converge past its
# initialisation transient before the latest bar.
MIN_WEEKLY_BARS = 30

# --- Paths -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
MANUAL_CSV = DATA_DIR / "index_weekly_manual.csv"   # highest-priority source
HOLDINGS_LEDGER = DATA_DIR / "holdings_ledger.csv"
STATE_FILE = DATA_DIR / "state.json"
JOURNAL_MD = DATA_DIR / "journal.md"
JOURNAL_JSONL = DATA_DIR / "journal.jsonl"
