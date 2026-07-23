"""Strategy parameters + the selectable regime registries.

Rulebook v2.1 default is Supertrend (1, 2.5) on NIFTY MIDSMALLCAP 400, but the
regime gate is a configurable "playground": the user picks an index and a filter
per cycle from the registries below. Every option is data-verified against
Yahoo Finance (see scripts/probe or the data_fetch notes).
"""

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Regime gate defaults --------------------------------------------------
SUPERTREND_ATR_PERIOD = 1
SUPERTREND_MULTIPLIER = 2.5

IST = ZoneInfo("Asia/Kolkata")


# --- Selectable index universe ---------------------------------------------
@dataclass(frozen=True)
class IndexOption:
    id: str
    label: str          # human name, e.g. "NIFTY MIDSMALLCAP 400"
    ticker: str         # Yahoo Finance symbol used to fetch weekly candles
    kind: str           # "index" (true index points) | "etf_proxy"
    note: str = ""


INDEX_OPTIONS: list[IndexOption] = [
    IndexOption("midsmall400", "NIFTY MIDSMALLCAP 400", "MIDSMALL.NS", "etf_proxy", "Mirae Asset ETF"),
    IndexOption("nifty50", "NIFTY 50", "^NSEI", "index"),
    IndexOption("nifty500", "NIFTY 500", "^CRSLDX", "index", "broad market"),
    IndexOption("banknifty", "NIFTY BANK", "^NSEBANK", "index"),
    IndexOption("midcap150", "NIFTY MIDCAP 150", "MID150BEES.NS", "etf_proxy", "Nippon ETF"),
    IndexOption("midcap50", "NIFTY MIDCAP 50", "^NSEMDCP50", "index"),
    IndexOption("smallcap250", "NIFTY SMALLCAP 250", "HDFCSML250.NS", "etf_proxy", "HDFC ETF"),
    IndexOption("microcap250", "NIFTY MICROCAP 250", "MOSMALL250.NS", "etf_proxy", "Motilal Oswal ETF"),
    IndexOption("next50", "NIFTY NEXT 50", "JUNIORBEES.NS", "etf_proxy", "Nippon ETF"),
]
DEFAULT_INDEX_ID = "midsmall400"


# --- Selectable regime filter (indicator + params) -------------------------
@dataclass(frozen=True)
class FilterOption:
    id: str
    label: str          # e.g. "Supertrend (1, 2.5)"
    indicator: str      # "supertrend" | "none"
    params: dict = field(default_factory=dict)


FILTER_OPTIONS: list[FilterOption] = [
    FilterOption("st_1_2_5", "Supertrend (1, 2.5)", "supertrend", {"period": 1, "multiplier": 2.5}),
    FilterOption("st_1_2", "Supertrend (1, 2)", "supertrend", {"period": 1, "multiplier": 2.0}),
    FilterOption("st_1_1", "Supertrend (1, 1)", "supertrend", {"period": 1, "multiplier": 1.0}),
    FilterOption("st_1_3", "Supertrend (1, 3)", "supertrend", {"period": 1, "multiplier": 3.0}),
    FilterOption("ema_34", "EMA 34", "ema", {"period": 34}),
    FilterOption("ema_68", "EMA 68", "ema", {"period": 68}),
    FilterOption("ema_100", "EMA 100", "ema", {"period": 100}),
    FilterOption("ema_150", "EMA 150", "ema", {"period": 150}),
    FilterOption("ema_200", "EMA 200", "ema", {"period": 200}),
    FilterOption("macd_35_70_12", "MACD (35, 70, 12)", "macd", {"fast": 35, "slow": 70, "signal": 12}),
    FilterOption("macd_50_100_15", "MACD (50, 100, 15)", "macd", {"fast": 50, "slow": 100, "signal": 15}),
    FilterOption("macd_75_150_15", "MACD (75, 150, 15)", "macd", {"fast": 75, "slow": 150, "signal": 15}),
    FilterOption("always_green", "Always GREEN (no filter)", "none", {}),
]
DEFAULT_FILTER_ID = "st_1_2_5"


def get_index_option(index_id: str | None) -> IndexOption:
    target = index_id or DEFAULT_INDEX_ID
    for opt in INDEX_OPTIONS:
        if opt.id == target:
            return opt
    raise KeyError(f"Unknown index_id {index_id!r}")


def get_filter_option(filter_id: str | None) -> FilterOption:
    target = filter_id or DEFAULT_FILTER_ID
    for opt in FILTER_OPTIONS:
        if opt.id == target:
            return opt
    raise KeyError(f"Unknown filter_id {filter_id!r}")


MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# Minimum weekly bars for the Supertrend recursion to converge past its
# initialisation transient before the latest bar.
MIN_WEEKLY_BARS = 30


# --- Part B: stock scoring universe + eligibility --------------------------
@dataclass(frozen=True)
class AltAssetOption:
    """Something to hold while the regime is RED, instead of idle cash.

    Backtest-only. The live ritual has no such rule — v2.2 says RED means cash —
    so this exists to TEST whether that rule should change, not to change it.
    """

    id: str
    label: str
    ticker: str
    note: str = ""
    first_session: str = ""      # earliest data; runs before this stay in cash


ALT_ASSET_OPTIONS: list[AltAssetOption] = [
    AltAssetOption("gold", "Gold (GOLDBEES)", "GOLDBEES.NS",
                   "Nippon gold ETF; ~0 correlation with equities", "2009-01-02"),
    AltAssetOption("gold_sbi", "Gold (SETFGOLD)", "SETFGOLD.NS",
                   "SBI gold ETF — a cross-check on GOLDBEES", "2009-05-18"),
    AltAssetOption("silver", "Silver (SILVERBEES)", "SILVERBEES.NS",
                   "far shorter history — 2022 onward only", "2022-02-04"),
    AltAssetOption("liquid", "Liquid fund (LIQUIDBEES)", "LIQUIDBEES.NS",
                   "the honest baseline: near-zero risk, ~6-7%/yr", "2009-01-02"),
]


def get_alt_asset_option(alt_id: str | None) -> AltAssetOption | None:
    if not alt_id:
        return None
    for o in ALT_ASSET_OPTIONS:
        if o.id == alt_id:
            return o
    raise KeyError(f"Unknown alt asset {alt_id!r}")


@dataclass(frozen=True)
class UniverseOption:
    id: str
    label: str
    source: str          # "holdings" | "nse_csv"
    nse_key: str = ""    # for nse_csv: the archives.nseindia.com file stem
    note: str = ""


UNIVERSE_OPTIONS: list[UniverseOption] = [
    UniverseOption("holdings", "Current Holdings", "holdings", note="Names in the ledger (fast)"),
    UniverseOption("nifty50", "NIFTY 50", "nse_csv", "ind_nifty50list", "50 names"),
    UniverseOption("next50", "NIFTY NEXT 50", "nse_csv", "ind_niftynext50list", "50 names"),
    UniverseOption("nifty100", "NIFTY 100", "nse_csv", "ind_nifty100list", "100 names"),
    UniverseOption("nifty200", "NIFTY 200", "nse_csv", "ind_nifty200list", "200 names"),
    UniverseOption("nifty500", "NIFTY 500", "nse_csv", "ind_nifty500list", "500 names (slow first fetch)"),
    UniverseOption("midcap150", "NIFTY MIDCAP 150", "nse_csv", "ind_niftymidcap150list", "150 names"),
    UniverseOption("smallcap250", "NIFTY SMALLCAP 250", "nse_csv", "ind_niftysmallcap250list", "250 names"),
    UniverseOption("midsmall400", "NIFTY MIDSMALLCAP 400", "nse_csv",
                   "ind_niftymidsmallcap400list", "400 names (slow first fetch)"),
    # NOTE: NSE names this file inconsistently — "..250_list" (underscore), not "..250list".
    UniverseOption("microcap250", "NIFTY MICROCAP 250", "nse_csv",
                   "ind_niftymicrocap250_list", "250 names (slow first fetch)"),
]
DEFAULT_UNIVERSE_ID = "holdings"

# Eligibility thresholds (Rulebook).
LIQUIDITY_FLOOR_CR = 5.0          # median daily traded value >= Rs 5 Cr
MEDIAN_TV_WINDOW = 20            # over the last 20 trading days
MIN_LISTING_DAYS = 252          # listed >= ~12 months
RUPEES_PER_CRORE = 1e7

# --- Part C: portfolio audit (exit rules) ----------------------------------
RANK_EXIT_THRESHOLD = 50         # holding ranked > 50 at Friday close -> exit Monday

# --- Part D: veto matrix (Spec v2.2, eliminate-only) ------------------------
VETO_RANK_WINDOW = 15            # iterate ranks 1-15 ONLY; unfilled slots stay cash
PLEDGING_MAX_PCT = 20.0          # promoter pledging > 20% -> strike
MIN_INTEREST_COVERAGE = 2.0      # interest coverage < 2 (non-financials) -> strike
TARGET_PORTFOLIO_MIN = 8         # target 8-12 names at ~10% each
TARGET_PORTFOLIO_MAX = 12
MAX_PER_SECTOR = 3
MAX_MICROCAP = 4
# Interest coverage is checked for non-financials only (Spec v2.2 check 2).
FINANCIAL_SECTOR_HINTS = ("financial", "bank", "insurance", "nbfc")
# Technical vetoes (checks 3/4/5) — all computed from cached price history.
DMA_TREND_PERIOD = 50            # price below its 50-DMA -> strike
DMA_EXTENSION_PERIOD = 20        # price > 30% above its 20-DMA -> strike (too extended)
MAX_EXTENSION_PCT = 30.0
RSI_PERIOD = 14
MAX_RSI = 82.0                   # RSI(14) > 82 -> strike (overbought)
FUNDAMENTALS_MAX_AGE_DAYS = 30   # re-fetch cached financials after this many days


def get_universe_option(universe_id: str | None) -> UniverseOption:
    target = universe_id or DEFAULT_UNIVERSE_ID
    for opt in UNIVERSE_OPTIONS:
        if opt.id == target:
            return opt
    raise KeyError(f"Unknown universe_id {universe_id!r}")

# --- Paths -----------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent          # src/backend/app
DATA_DIR = APP_DIR / "data"
CYCLES_DIR = DATA_DIR / "cycles"
CURRENT_CYCLE_FILE = DATA_DIR / "_current_cycle.json"
HOLDINGS_LEDGER = DATA_DIR / "holdings_ledger.csv"
# Optional manual override: drop <ticker>.csv (date,open,high,low,close) here to
# feed the gate from a hand-exported file instead of Yahoo, per index.
MANUAL_DIR = DATA_DIR / "index_manual"
PRICES_DIR = DATA_DIR / "prices"          # per-stock OHLCV cache (Part B scoring)
UNIVERSES_DIR = DATA_DIR / "universes"    # cached NSE constituent lists
FUNDAMENTALS_CACHE = DATA_DIR / "fundamentals_cache.json"   # interest-coverage cache
SURVEILLANCE_CACHE = DATA_DIR / "surveillance_cache.json"   # NSE ASM/GSM lists (daily)
# The durable weekly journal — survives cycle deletion (Rulebook: the journal is
# the permanent record, individual cycle files are working state).
JOURNAL_JSONL = DATA_DIR / "journal.jsonl"
JOURNAL_MD = DATA_DIR / "journal.md"
