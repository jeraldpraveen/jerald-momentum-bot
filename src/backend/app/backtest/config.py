"""Backtest configuration and paths — deliberately separate from the live app.

Everything the backtest reads or writes lives under `data/backtest/`. It never
touches `cycles/`, `holdings_ledger.csv` or `journal.jsonl`, so a research run
can never corrupt the Friday ritual's state. The price cache is its own store
too: the backtest needs decades of history and immutable bars, which is a
different problem from the live app's "is today's close in yet?".
"""

from __future__ import annotations

from .. import config as app_config

# --- paths -----------------------------------------------------------------
BACKTEST_DIR = app_config.DATA_DIR / "backtest"
PRICES_DIR = BACKTEST_DIR / "prices"        # per-stock OHLCV, full history
UNIVERSES_DIR = BACKTEST_DIR / "universes"  # universe definition CSVs
RUNS_DIR = BACKTEST_DIR / "runs"            # one JSON per run (config + summary)
RESULTS_DIR = BACKTEST_DIR / "results"      # equity curves, trades, per-week rows

ALL_DIRS = [BACKTEST_DIR, PRICES_DIR, UNIVERSES_DIR, RUNS_DIR, RESULTS_DIR]


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# --- simulation defaults ---------------------------------------------------
REBALANCE_FREQ = "W-FRI"        # the Friday ritual's cadence
STOP_MULTIPLIER = 0.80          # 20% stop, same as the live plan
POSITION_WEIGHT_PCT = 10.0      # ~10% per name
STARTING_CAPITAL = 1_000_000.0  # 10 lakh, a round default the user can change

# The live Monday plan DEFERS a buy it cannot fund in full ("~10% positions, not
# more, smaller ones"). The simulators follow the same rule: a buy that cannot
# reach this fraction of its target size waits for next week instead of being
# opened as a sliver with whatever cash is left. The 5% allowance covers
# whole-share rounding and charges, nothing more.
MIN_FILL_FRACTION = 0.95

# Costs. Indian equity delivery: brokerage is often zero, but STT, exchange
# charges, stamp duty and GST are not — and slippage on mid/microcaps is the
# larger cost. Both are configurable per run.
COST_BPS = 30.0                 # round-trip-ish transaction cost, basis points
SLIPPAGE_BPS = 25.0             # adverse fill vs the reference price

# Spec v2.3 (amendment 1): idle cash sits in a liquid or overnight fund, so the
# simulation credits it a fixed yield by default. 6.5% is the middle of the
# 5-8% band the July 2026 runs bracketed; set it to 0 to model truly idle cash.
CASH_YIELD_PCT = 6.5

# Spec v2.3 (amendment 4): results are after tax unless a run opts out. See
# `tax.py` for the model and its stated simplifications.
APPLY_TAX = True

# Warm-up: the score needs 1 year of candles (12 x 21) and the regime gate needs
# enough weekly bars for the Supertrend. Weeks before this are not simulated.
MIN_WARMUP_DAYS = 300
