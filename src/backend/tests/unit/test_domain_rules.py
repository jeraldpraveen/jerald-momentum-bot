"""Domain rules, checked against hand arithmetic.

Every expected number here is worked out by hand in the comment beside it, so
the test fails if the RULE is wrong — not merely if the code changes. Where the
code does something the Rulebook would not obviously want, the test is marked
`xfail(strict=True)` with the reason: it documents a known caveat and will
start failing loudly the day someone fixes it.
"""

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from app import config
from app.domain import indicators, momentum_score as ms, scoring_formula
from app.domain.strategy import (
    DATA_GAP, DROPPED, SCREENED_OUT, Position, RankedName, apply_caps, audit_holdings,
    build_candidates, canonical_sector, decide_regime, evaluate_technical,
)
from app.domain.strategy.scoring import as_of_or_today, median_traded_value_cr
from app.domain.supertrend import compute_supertrend, wilder_atr
from app.schemas.scanner import ScannerCandidate, ScannerFilters

FORMULA = scoring_formula.compile_formula(scoring_formula.FROZEN_FORMULA)


def ohlc(rows):
    """rows = [(high, low, close)] -> weekly candle frame."""
    h, l, c = zip(*rows)
    return pd.DataFrame({"open": c, "high": h, "low": l, "close": c},
                        index=pd.date_range("2024-01-05", periods=len(rows), freq="W-FRI"))


def daily(closes, volume=5_000_000.0, start="2022-01-03"):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volume},
        index=pd.bdate_range(start, periods=len(closes)))


# --- true range / Wilder ATR --------------------------------------------------

# bar:      0        1        2        3        4 (gaps up from a close of 20)
BARS = [(11, 9, 10), (14, 10, 12), (18, 12, 15), (23, 15, 20), (60, 50, 58)]


def _arrays(rows):
    h, l, c = (np.array(x, dtype=float) for x in zip(*rows))
    return h, l, c


def test_true_range_uses_the_previous_close_on_a_gap():
    """Bar 4's own range is 10, but it opened 30 above the prior close of 20:
    the true range is high - previous close = 40."""
    tr = wilder_atr(*_arrays(BARS), period=1)
    assert list(tr) == [2.0, 4.0, 6.0, 8.0, 40.0]


def test_wilder_atr_seeds_with_a_simple_average_then_smooths():
    atr = wilder_atr(*_arrays(BARS), period=3)

    assert np.isnan(atr[0]) and np.isnan(atr[1])        # undefined before 3 bars
    assert atr[2] == pytest.approx((2 + 4 + 6) / 3)     # = 4
    assert atr[3] == pytest.approx((4 * 2 + 8) / 3)     # = 5.3333
    assert atr[4] == pytest.approx((16 / 3 * 2 + 40) / 3)   # = 16.8889


def test_supertrend_with_a_longer_atr_starts_down_then_flips_on_a_breakout():
    st = compute_supertrend(ohlc(BARS), period=3, multiplier=2.5)

    # Nothing is decided before the ATR exists.
    assert list(st["trend"].iloc[:3]) == [0, 0, 0] and st["supertrend"].iloc[:3].isna().all()
    # Bar 3: upper band = 19 + 2.5 x 5.3333 = 32.33; close 20 is below it -> down.
    assert st["trend"].iloc[3] == -1
    assert st["supertrend"].iloc[3] == pytest.approx(19 + 2.5 * 16 / 3)
    # Bar 4: the upper band is held at 32.33 (the new one is higher); close 58
    # breaks it -> up, and the line becomes the lower band 55 - 2.5 x 16.8889.
    assert st["trend"].iloc[4] == 1
    assert st["supertrend"].iloc[4] == pytest.approx(55 - 2.5 * (16 / 3 * 2 + 40) / 3)


def test_supertrend_refuses_too_little_history():
    with pytest.raises(ValueError, match="Need at least 2 bars, got 1"):
        compute_supertrend(ohlc(BARS[:1]))
    with pytest.raises(ValueError, match="Need at least 4 bars, got 3"):
        compute_supertrend(ohlc(BARS[:3]), period=3)


def _steps(closes):
    """Weekly candles one point either side of each close."""
    return [(c + 1, c - 1, c) for c in closes]


# Closes climb 4 a week. True range is 5 (high minus the prior close), so the
# bands sit 12.5 either side of the close.
CLIMB = [100, 104, 108, 112, 116, 120, 124]


def test_supertrend_1_2_5_ratchets_the_line_up_under_a_rising_market():
    st = compute_supertrend(ohlc(_steps(CLIMB)), period=1, multiplier=2.5)

    # Bar 1 sets the upper band at 104 + 12.5 = 116.5 and it is HELD while prices
    # rise (the band only ever ratchets down). The first close above it is 120.
    assert list(st["trend"]) == [0, -1, -1, -1, -1, 1, 1]
    assert st["supertrend"].iloc[4] == pytest.approx(116.5)
    # Once up, the line is the lower band, ratcheting up 4 a week: 120 - 12.5...
    assert st["supertrend"].iloc[5] == pytest.approx(107.5)
    assert st["supertrend"].iloc[6] == pytest.approx(111.5)


def test_an_ugly_week_that_still_closes_above_the_line_stays_green():
    """A wide-range down week pushes its OWN lower band far below, but the line
    never moves down in an uptrend — it stays at 111.5, and 114 is above it."""
    st = compute_supertrend(ohlc(_steps(CLIMB) + [(125, 113, 114)]), period=1, multiplier=2.5)
    assert st["trend"].iloc[-1] == 1
    assert st["supertrend"].iloc[-1] == pytest.approx(111.5)


def test_one_close_below_the_line_turns_the_regime_red():
    st = compute_supertrend(ohlc(_steps(CLIMB) + [(125, 108, 110)]), period=1, multiplier=2.5)
    assert st["trend"].iloc[-1] == -1                    # 110 < 111.5


def test_the_gate_starts_red_and_turns_green_on_the_first_close_above_the_first_band():
    """The recursion seeds DOWN. A gentle climb (about 0.9 a week, range 2) has
    its upper band fixed by bar 1 at 100.909 + 5 = 105.909; bar 7 is the first
    close above it."""
    closes = np.linspace(100, 110, 12)
    st = compute_supertrend(ohlc(_steps(closes)), period=1, multiplier=2.5)
    assert closes[6] < 105.909 < closes[7]
    assert list(st["trend"]) == [0] + [-1] * 6 + [1] * 5


def test_a_market_that_never_breaks_out_reads_red_from_the_start():
    """KNOWN BEHAVIOUR, pinned: with no breakout there is nothing to turn the
    seed around, so a flat index is RED however long it runs. The live gate
    needs MIN_WEEKLY_BARS of history so that this start-up state has washed out."""
    st = compute_supertrend(ohlc(_steps([100.0] * 40)), period=1, multiplier=2.5)
    assert set(st["trend"].iloc[1:]) == {-1}
    assert config.MIN_WEEKLY_BARS == 30


# --- regime rule ----------------------------------------------------------------

def test_decide_regime_rejects_an_unknown_indicator():
    filt = config.FilterOption("x", "X", "astrology", {})
    with pytest.raises(ValueError, match="Unsupported indicator 'astrology'"):
        decide_regime(ohlc(BARS), filt)


def test_always_green_ignores_the_candles_entirely():
    assert decide_regime(ohlc(BARS), config.get_filter_option("always_green")) == \
        ("GREEN", "", None)


# --- indicators -----------------------------------------------------------------

def test_sma_is_none_without_a_full_window():
    s = pd.Series([1.0, 2.0, 3.0])
    assert indicators.sma(s, 4) is None
    assert indicators.sma(s, 3) == pytest.approx(2.0)


def test_rsi_needs_period_plus_one_closes():
    assert indicators.rsi(pd.Series(range(14), dtype=float), 14) is None
    assert indicators.rsi(pd.Series(range(15), dtype=float), 14) == 100.0   # never fell


# Regression test. Before the fix — FIXED CAVEAT: a completely FLAT price series has no
# gains and no losses, so RSI is 0/0 — undefined. The code returns 100, which the
# technical veto reads as 'overbought' and STRIKES. A halted or illiquid name is struck
# for the wrong reason instead of being flagged unverified.
def test_rsi_of_a_flat_series_is_undefined_not_overbought():
    assert indicators.rsi(pd.Series([50.0] * 40), 14) is None


# --- the legacy momentum score ---------------------------------------------------

def test_trailing_volatility_needs_at_least_two_returns():
    assert math.isnan(ms.trailing_volatility(pd.Series([1.0, 2.0, 3.0]), 1))


def test_trailing_return_is_nan_without_enough_history():
    s = pd.Series([100.0, 110.0])
    assert math.isnan(ms.trailing_return(s, 2)) and math.isnan(ms.trailing_return(s, 0))
    assert ms.trailing_return(s, 1) == pytest.approx(0.10)


def test_a_flat_stock_has_no_score_rather_than_infinity():
    """Zero volatility would divide by zero; the score must be NaN, never inf —
    an infinite score would rank a halted stock first."""
    flat = pd.Series([100.0] * 300)
    assert math.isnan(ms.momentum_score(flat))
    assert math.isnan(scoring_formula.score(flat, FORMULA))


def test_rank_universe_puts_unscorable_names_last():
    rising = pd.Series(100 * np.cumprod(1 + np.random.default_rng(1).normal(0.002, 0.01, 300)))
    table = ms.rank_universe({"FLAT": pd.Series([100.0] * 300), "UP": rising})
    assert list(table["symbol"]) == ["UP", "FLAT"]
    assert list(table["rank"]) == [1, 2] and math.isnan(table["score"].iloc[1])


# --- liquidity -------------------------------------------------------------------

def test_median_traded_value_is_in_crores():
    df = daily([100.0] * 30, volume=2_000_000.0)         # 100 x 20 lakh = Rs 20 crore a day
    assert median_traded_value_cr(df, 20) == pytest.approx(20.0)


def test_median_traded_value_is_none_without_data():
    assert median_traded_value_cr(daily([]), 20) is None
    assert median_traded_value_cr(daily([100.0] * 5), 0) is None      # empty window


def test_the_liquidity_floor_is_five_crore_inclusive():
    members = [("OK.NS", "X", ""), ("THIN.NS", "X", "")]
    closes = 100 * np.cumprod(1 + np.random.default_rng(2).normal(0.001, 0.01, 300))
    prices = {
        "OK.NS": daily(closes, volume=5e7 / closes[-1]),          # exactly 5 crore today
        "THIN.NS": daily(closes, volume=4.99e7 / closes[-1]),
    }
    # The median of the last 20 sessions is what counts, so pin the window to 1.
    rows = {c.symbol: c for c in build_candidates(members, prices, FORMULA, median_tv_window=1)}
    assert rows["OK"].eligible and rows["OK"].median_tv_cr == pytest.approx(5.0)
    assert not rows["THIN"].eligible and rows["THIN"].flags == ["illiquid"]


# --- scoring / ranking -----------------------------------------------------------

def _trend(drift, n=300, seed=0):
    return daily(100 * np.cumprod(1 + np.random.default_rng(seed).normal(drift, 0.01, n)))


def test_price_range_filters_flag_and_exclude():
    members = [("A.NS", "X", ""), ("B.NS", "X", ""), ("C.NS", "X", "")]
    prices = {"A.NS": daily(np.linspace(40, 50, 300)),       # last 50
              "B.NS": daily(np.linspace(80, 100, 300)),      # last 100
              "C.NS": daily(np.linspace(400, 500, 300))}     # last 500
    out = {c.symbol: c for c in build_candidates(
        members, prices, FORMULA, ScannerFilters(price_min=60, price_max=200))}

    assert out["A"].flags == ["below_price_min"] and out["A"].rank is None
    assert out["B"].eligible and out["B"].rank == 1
    assert out["C"].flags == ["above_price_max"] and out["C"].rank is None


def test_ranks_are_dense_best_score_first_and_ineligible_names_sort_after():
    members = [(f"S{i}.NS", "X", "") for i in range(3)] + [("NEW.NS", "X", "")]
    prices = {"S0.NS": _trend(0.0005, seed=1), "S1.NS": _trend(0.003, seed=2),
              "S2.NS": _trend(0.0015, seed=3), "NEW.NS": _trend(0.004, n=100, seed=4)}
    out = build_candidates(members, prices, FORMULA)

    ranked = [c for c in out if c.rank is not None]
    assert [c.rank for c in ranked] == [1, 2, 3]
    assert [c.score for c in ranked] == sorted((c.score for c in ranked), reverse=True)
    assert out[-1].symbol == "NEW" and "listed<12m" in out[-1].flags


def test_a_name_with_no_prices_is_listed_as_no_data():
    out = build_candidates([("GONE.NS", "X", "Gone Ltd")], {}, FORMULA)
    assert out[0].flags == ["no_data"] and out[0].rank is None and out[0].close is None


def test_a_symbol_without_the_ns_suffix_is_kept_as_is():
    out = build_candidates([("INDEX", "X", "")], {"INDEX": _trend(0.002)}, FORMULA)
    assert out[0].symbol == "INDEX"


def test_as_of_or_today_prefers_the_filter_date():
    d = dt.date(2026, 11, 13)
    assert as_of_or_today(ScannerFilters(as_of=d)) == d
    assert as_of_or_today(None) == dt.date.today()
    assert as_of_or_today(ScannerFilters()) == dt.date.today()


# Regression test. Before the fix — FIXED CAVEAT (stale prices): the ranking never
# checks HOW OLD a name's last bar is. A stock whose history stops four months before
# the as-of date — a halt, or a failed price refresh — is scored on that stale series
# and ranked as if it were current. The scanner then reports 'as of' the requested date.
def test_a_name_whose_prices_stop_months_before_the_as_of_date_is_not_ranked():
    as_of = dt.date(2026, 11, 13)
    stale = _trend(0.004, seed=5)                                  # ends early 2023
    out = build_candidates([("OLD.NS", "X", "")], {"OLD.NS": stale}, FORMULA,
                           ScannerFilters(as_of=as_of))
    assert (as_of - stale.index[-1].date()).days > 120
    assert out[0].rank is None


# --- portfolio audit --------------------------------------------------------------

def _cand(symbol, rank, eligible=True, flags=()):
    return ScannerCandidate(symbol=symbol, sector="X", rank=rank, score=1.0,
                            eligible=eligible, flags=list(flags))


def test_rank_exit_is_strictly_worse_than_fifty():
    rows = audit_holdings(
        [Position("AT50", "X"), Position("AT51", "X")],
        [_cand("AT50", 50), _cand("AT51", 51)])

    assert [(r.symbol, r.exit_flagged, r.reasons) for r in rows] == [
        ("AT50", False, []), ("AT51", True, ["rank_exit"])]


def test_a_holding_absent_from_the_scan_is_dropped():
    rows = audit_holdings([Position("GONE", "X")], [_cand("OTHER", 1)])
    assert rows[0].exit_flagged and rows[0].reasons == [DROPPED]
    assert rows[0].in_universe is False and rows[0].rank is None


# Regression test. Before the fix — FIXED CAVEAT (data-safeguard): a holding whose price
# download FAILED this week comes back from the scan flagged 'no_data' with no rank —
# and the audit treats any unranked name as 'dropped_from_universe' and flags it for a
# Monday sell. A data outage is reported as an index deletion. The Rulebook says a
# missing number is never a strike.
def test_a_holding_with_no_price_data_is_not_flagged_as_dropped_from_the_index():
    rows = audit_holdings([Position("HELD", "X")],
                          [_cand("HELD", None, eligible=False, flags=["no_data"])])
    assert not rows[0].exit_flagged
    assert rows[0].reasons == [DATA_GAP] and rows[0].in_universe is True


def test_a_holding_with_a_stale_price_is_kept_for_the_same_reason():
    rows = audit_holdings([Position("HELD", "X")],
                          [_cand("HELD", None, eligible=False, flags=["stale_price", "illiquid"])])
    assert not rows[0].exit_flagged and rows[0].reasons == [DATA_GAP]


# Regression test. Before the fix — FIXED CAVEAT: the scan's optional sector / price-
# range screens make a name ineligible, and the audit cannot tell that from a real exit
# signal. Run the scan with a sector filter and every holding outside it is flagged
# 'dropped_from_universe' and queued for sale.
def test_a_holding_screened_out_by_a_discretionary_filter_is_not_an_exit():
    rows = audit_holdings([Position("HELD", "X")],
                          [_cand("HELD", None, eligible=False, flags=["sector_excluded"])])
    assert not rows[0].exit_flagged


# --- risk caps --------------------------------------------------------------------

def _names(*specs):
    return [RankedName(sym, rank, sector) for sym, rank, sector in specs]


def test_sector_cap_counts_kept_holdings_plus_buys():
    held = [Position("H1", "Auto"), Position("H2", "Auto")]
    res = apply_caps(_names(("A", 1, "Auto"), ("B", 2, "Auto"), ("C", 3, "IT")), held, set())

    assert [b.symbol for b in res.buys] == ["A", "C"]          # A is the third Auto name
    assert [(s.symbol, s.reason) for s in res.skipped] == [("B", "sector_cap")]
    # Counted under NSE's industry names, whatever label each row carried.
    assert res.sector_counts == {"Automobile and Auto Components": 3,
                                 "Information Technology": 1}


def test_microcap_cap_is_four_including_held_microcaps():
    micro = {"H1", "H2", "H3", "M1", "M2"}
    held = [Position(s, f"S{i}") for i, s in enumerate(("H1", "H2", "H3"))]
    res = apply_caps(_names(("M1", 1, "A"), ("M2", 2, "B"), ("BIG", 3, "C")), held, micro)

    assert [b.symbol for b in res.buys] == ["M1", "BIG"]
    assert [(s.symbol, s.reason) for s in res.skipped] == [("M2", "microcap_cap")]
    assert res.microcap_count == 4


def test_a_full_portfolio_buys_nothing():
    held = [Position(f"H{i}", f"S{i}") for i in range(12)]
    res = apply_caps(_names(("A", 1, "Z")), held, set())
    assert res.open_slots == 0 and res.buys == [] and res.skipped[0].reason == "slots_full"


def test_skipping_a_capped_name_does_not_reach_past_the_candidate_list():
    """A skipped candidate leaves its slot EMPTY — the loop never substitutes."""
    held = [Position(f"H{i}", "Auto") for i in range(3)]              # Auto already full
    res = apply_caps(_names(("A", 1, "Auto"), ("B", 2, "Auto")), held, set())
    assert res.buys == [] and res.unfilled_slots == 9


# Regression test. Before the fix — FIXED CAVEAT: the sector cap compares sector LABELS
# as text. The NSE constituent file says 'Automobile and Auto Components' and 'Capital
# Goods'; the ledger held 'Auto Components', 'Auto' and 'Industrial Machinery' for the
# same companies. Holdings labelled differently from the scan are invisible to the cap,
# so a fourth name in the same real sector is bought.
def test_the_sector_cap_binds_across_differently_spelled_sector_labels():
    held = [Position("SANSERA", "Auto Components"), Position("SONACOMS", "Auto Components"),
            Position("ATHERENERG", "Auto")]
    res = apply_caps(_names(("NEWAUTO", 1, "Automobile and Auto Components")), held, set())
    assert res.buys == []


def test_twelve_slots_at_ten_percent_is_more_than_the_capital():
    """KNOWN TENSION in the Rulebook, pinned: the target is 8-12 names and each
    is sized at ~10%, so a full buy list asks for 120% of the portfolio. The
    Monday plan resolves it by funding in rank order and deferring the rest."""
    res = apply_caps(_names(*[(f"N{i}", i + 1, f"S{i}") for i in range(15)]), [], set())
    assert len(res.buys) == config.TARGET_PORTFOLIO_MAX == 12
    assert sum(b.weight_pct for b in res.buys) == pytest.approx(120.0)


# --- technical veto ----------------------------------------------------------------

def _tech(closes):
    return evaluate_technical([RankedName("X", 1, "S")], {"X.NS": daily(closes)})[0]


def test_price_exactly_on_the_50_dma_passes():
    row = _tech([100.0] * 60)
    assert row.trend.verdict == "pass" and row.dma_50 == 100.0


def test_price_below_the_50_dma_strikes():
    row = _tech([100.0] * 59 + [99.0])
    assert row.struck and row.trend.verdict == "strike"


def test_extension_strikes_only_above_thirty_percent():
    at_limit = _tech([100.0] * 59 + [100.0 * 1.3 * 19 / (20 - 1.3)])   # exactly +30%
    over = _tech([100.0] * 59 + [150.0])
    assert at_limit.extension_pct == pytest.approx(30.0, abs=0.01)
    assert at_limit.extension.verdict == "pass"
    assert over.extension.verdict == "strike"


def test_missing_history_is_unverified_and_passes():
    """The data-safeguard: a number that cannot be computed never strikes."""
    row = _tech([100.0, 101.0, 102.0])
    assert not row.struck
    assert {row.trend.verdict, row.extension.verdict, row.overbought.verdict} == {"unverified"}
    assert len(row.flags) == 3


def test_a_name_with_no_prices_at_all_is_unverified_not_struck():
    row = evaluate_technical([RankedName("X", 1, "S")], {})[0]
    assert not row.struck and row.close is None and len(row.flags) == 3


# --- more on the fixed caveats --------------------------------------------------

@pytest.mark.parametrize("flags", [["below_price_min"], ["above_price_max"],
                                   ["sector_excluded", "above_price_max"]])
def test_every_discretionary_screen_keeps_the_holding_and_says_why(flags):
    rows = audit_holdings([Position("HELD", "X")],
                          [_cand("HELD", None, eligible=False, flags=flags)])
    assert not rows[0].exit_flagged and rows[0].reasons == [SCREENED_OUT]


@pytest.mark.parametrize("flags", [["illiquid"], ["listed<12m"], ["insufficient_history"],
                                   ["illiquid", "sector_excluded"], []])
def test_a_genuinely_ineligible_holding_is_still_dropped(flags):
    """Illiquid or too new means 'we would not buy it today' — that IS an exit,
    and a discretionary screen on top does not rescue it."""
    rows = audit_holdings([Position("HELD", "X")],
                          [_cand("HELD", None, eligible=False, flags=flags)])
    assert rows[0].exit_flagged and rows[0].reasons == [DROPPED]


def test_a_price_within_a_week_of_the_as_of_date_is_current():
    as_of = dt.date(2026, 11, 16)                                 # a Monday
    frame = _trend(0.003, seed=6)
    for age, stale in ((0, False), (3, False), (7, False), (8, True)):
        frame.index = pd.bdate_range(end=as_of - dt.timedelta(days=age), periods=len(frame))
        real_age = (as_of - frame.index[-1].date()).days
        out = build_candidates([("A.NS", "X", "")], {"A.NS": frame}, FORMULA,
                               ScannerFilters(as_of=as_of))[0]
        assert ("stale_price" in out.flags) is (real_age > 7), (age, real_age)
        assert out.eligible is not (real_age > 7)


def test_staleness_is_not_judged_without_an_as_of_date_and_the_limit_is_adjustable():
    old = _trend(0.003, seed=6)                                    # ends early 2023
    assert build_candidates([("A.NS", "X", "")], {"A.NS": old}, FORMULA)[0].eligible
    lenient = build_candidates([("A.NS", "X", "")], {"A.NS": old}, FORMULA,
                               ScannerFilters(as_of=dt.date(2026, 11, 13)),
                               max_price_age_days=10_000)[0]
    assert lenient.eligible and config.MAX_PRICE_AGE_DAYS == 7


@pytest.mark.parametrize("label,expected", [
    ("Automobile and Auto Components", "Automobile and Auto Components"),   # NSE's own name
    ("capital goods", "Capital Goods"),                                     # case-insensitive
    ("Auto Components", "Automobile and Auto Components"),
    ("Auto", "Automobile and Auto Components"),
    ("Industrial Machinery", "Capital Goods"),
    ("Industrial Automation", "Capital Goods"),                   # not Automobile
    ("Banking", "Financial Services"),
    ("NBFC", "Financial Services"),
    ("Pharma", "Healthcare"),
    ("IT", "Information Technology"),
    ("IT Services", "Information Technology"),                    # not Services
    ("Software", "Information Technology"),
    ("FMCG", "Fast Moving Consumer Goods"),
    ("Cement", "Construction Materials"),
    ("Construction Materials", "Construction Materials"),         # not Construction
    ("Infrastructure", "Construction"),
    ("Steel", "Metals & Mining"),
    ("Oil & Gas", "Oil Gas & Consumable Fuels"),
    ("Power Generation", "Power"),
    ("Real Estate", "Realty"),
    ("Telecom", "Telecommunication"),
    ("Textile", "Textiles"),
    ("Media", "Media Entertainment & Publication"),
    ("Retail", "Consumer Services"),
    ("Consumer Durables", "Consumer Durables"),
    ("Appliances", "Consumer Durables"),
    ("Chemicals", "Chemicals"),
    ("Fertilisers", "Chemicals"),
    ("Utilities", "Utilities"),
    ("Paper", "Forest Materials"),
    ("Logistics", "Services"),
    ("Diversified", "Diversified"),
    ("  Sector0  ", "Sector0"),                                   # unknown: its own sector
    ("", ""), (None, ""), ("nan", ""),
])
def test_sector_labels_map_onto_nse_industries(label, expected):
    assert canonical_sector(label) == expected


def test_unknown_sector_labels_stay_separate_sectors():
    held = [Position("H1", "Widgets"), Position("H2", "Widgets"), Position("H3", "Widgets")]
    res = apply_caps(_names(("A", 1, "Widgets"), ("B", 2, "Gadgets")), held, set())
    assert [b.symbol for b in res.buys] == ["B"]
    assert res.sector_counts == {"Widgets": 3, "Gadgets": 1}


def test_an_rsi_that_only_ever_rose_is_still_one_hundred():
    assert indicators.rsi(pd.Series(np.linspace(100, 130, 40)), 14) == 100.0
