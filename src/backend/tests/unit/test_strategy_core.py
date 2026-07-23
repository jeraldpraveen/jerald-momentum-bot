"""The pure strategy core — the rules, tested without any app state.

These tests exist because the core has two callers: the live Friday-ritual
services and the backtest engine. If the core stays pure and correct, the
backtest provably exercises the same rules the user trades.
"""

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from app import config
from app.domain import strategy
from app.domain.strategy import Position, RankedName
from app.schemas.scanner import ScannerCandidate, ScannerFilters

CORE_DIR = pathlib.Path(strategy.__file__).parent
FORBIDDEN = ("services", "repositories", "routers", "integrations")


# --- the isolation contract -------------------------------------------------

def test_strategy_core_has_no_io():
    """The core may not import app state, disk or network layers.

    This is the constraint that makes it shareable. If it ever imports a
    repository, the backtest starts depending on the live ledger.
    """
    offenders: list[str] = []
    for path in CORE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                if any(part in m.split(".") for part in FORBIDDEN):
                    offenders.append(f"{path.name}: {m}")
    assert offenders == [], f"strategy core must stay pure, found: {offenders}"


# --- regime -----------------------------------------------------------------

def _weekly(closes):
    idx = pd.date_range("2024-01-05", periods=len(closes), freq="W-FRI")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.02, "low": c * 0.98, "close": c}, index=idx)


def test_regime_none_filter_is_always_green():
    filt = config.get_filter_option("always_green")
    regime, label, value = strategy.decide_regime(_weekly([100] * 10), filt)
    assert regime == "GREEN" and label == "" and value is None


def test_regime_uptrend_is_green_downtrend_is_red():
    filt = config.get_filter_option(config.DEFAULT_FILTER_ID)
    up, _, _ = strategy.decide_regime(_weekly(np.linspace(100, 200, 60)), filt)
    down, _, _ = strategy.decide_regime(_weekly(np.linspace(200, 100, 60)), filt)
    assert (up, down) == ("GREEN", "RED")


def test_regime_rejects_an_unknown_indicator():
    bad = config.FilterOption(id="x", label="x", indicator="astrology", params={})
    with pytest.raises(ValueError, match="astrology"):
        strategy.decide_regime(_weekly([100] * 10), bad)


# --- scoring ----------------------------------------------------------------

def _ohlcv(closes, volume=5_000_000):
    c = np.asarray(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(c), freq="D")
    return pd.DataFrame(
        {"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": float(volume)},
        index=idx,
    )


@pytest.fixture
def compiled():
    from app.domain import scoring_formula
    return scoring_formula.compile_formula(scoring_formula.DEFAULT_FORMULA)


def _members(*symbols):
    return [(f"{s}.NS", "Auto", s) for s in symbols]


def test_ranking_is_by_score_descending(compiled):
    rng = np.random.default_rng(3)
    prices = {
        "STRONG.NS": _ohlcv(100 * np.cumprod(1 + rng.normal(0.002, 0.01, 400))),
        "WEAK.NS": _ohlcv(100 * np.cumprod(1 + rng.normal(-0.001, 0.01, 400))),
    }
    out = strategy.build_candidates(_members("STRONG", "WEAK"), prices, compiled)
    by = {c.symbol: c for c in out}
    assert by["STRONG"].score > by["WEAK"].score
    assert by["STRONG"].rank == 1 and by["WEAK"].rank == 2


def test_missing_prices_are_flagged_not_dropped(compiled):
    out = strategy.build_candidates(_members("GHOST"), {}, compiled)
    assert len(out) == 1
    assert out[0].eligible is False and out[0].flags == ["no_data"]
    assert out[0].rank is None


def test_illiquid_and_too_new_names_lose_their_rank(compiled):
    rng = np.random.default_rng(5)
    path = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, 400))
    prices = {
        "THIN.NS": _ohlcv(path, volume=100),          # tiny traded value
        "NEW.NS": _ohlcv(path[:100]),                 # < 252 sessions
        "GOOD.NS": _ohlcv(path),
    }
    out = {c.symbol: c for c in
           strategy.build_candidates(_members("THIN", "NEW", "GOOD"), prices, compiled)}
    assert "illiquid" in out["THIN"].flags and out["THIN"].rank is None
    assert "listed<12m" in out["NEW"].flags and out["NEW"].rank is None
    assert out["GOOD"].rank == 1


def test_sector_and_price_filters_exclude_without_deleting(compiled):
    rng = np.random.default_rng(11)
    prices = {t: _ohlcv(100 * np.cumprod(1 + rng.normal(0.001, 0.01, 400)))
              for t in ("A.NS", "B.NS")}
    members = [("A.NS", "Auto", "A"), ("B.NS", "Banking", "B")]
    out = {c.symbol: c for c in strategy.build_candidates(
        members, prices, compiled, ScannerFilters(sectors=["Auto"]))}
    assert out["A"].eligible and "sector_excluded" in out["B"].flags
    assert len(out) == 2          # excluded, not removed — the UI shows why


def test_liquidity_floor_is_overridable_for_a_sweep(compiled):
    rng = np.random.default_rng(7)
    prices = {"THIN.NS": _ohlcv(100 * np.cumprod(1 + rng.normal(0.001, 0.01, 400)), volume=100)}
    strict = strategy.build_candidates(_members("THIN"), prices, compiled)[0]
    loose = strategy.build_candidates(_members("THIN"), prices, compiled,
                                      liquidity_floor_cr=0.0)[0]
    assert strict.eligible is False and loose.eligible is True


# --- audit ------------------------------------------------------------------

def _cand(symbol, rank, score=1.0, eligible=True):
    return ScannerCandidate(symbol=symbol, company="", sector="Auto", score=score,
                            rank=rank, eligible=eligible)


def test_audit_keeps_a_top_ranked_holding():
    rows = strategy.audit_holdings([Position("AAA", "Auto")], [_cand("AAA", 3)])
    assert rows[0].exit_flagged is False and rows[0].reasons == []


def test_audit_exits_a_holding_ranked_past_the_threshold():
    rows = strategy.audit_holdings([Position("AAA", "Auto")], [_cand("AAA", 51)])
    assert rows[0].exit_flagged and rows[0].reasons == [strategy.RANK_EXIT]


def test_audit_treats_a_name_missing_from_the_universe_as_dropped():
    rows = strategy.audit_holdings([Position("GONE", "Auto")], [_cand("AAA", 1)])
    assert rows[0].in_universe is False
    assert rows[0].reasons == [strategy.DROPPED]


def test_audit_treats_an_unranked_ineligible_holding_as_dropped():
    """Illiquid or newly listed means "we would not buy it today" — so exit."""
    rows = strategy.audit_holdings(
        [Position("THIN", "Auto")], [_cand("THIN", None, eligible=False)])
    assert rows[0].reasons == [strategy.DROPPED]


def test_audit_threshold_is_overridable():
    rows = strategy.audit_holdings([Position("AAA")], [_cand("AAA", 30)], rank_threshold=20)
    assert rows[0].reasons == [strategy.RANK_EXIT]


# --- technical --------------------------------------------------------------

def test_technical_strikes_a_name_below_its_50_dma():
    falling = {"DOWN.NS": _ohlcv(np.linspace(200, 100, 200))}
    row = strategy.evaluate_technical([RankedName("DOWN", 1, "Auto")], falling)[0]
    assert row.struck and any("below 50-DMA" in s for s in row.strikes)


def test_technical_strikes_an_overbought_name():
    """A monotonic ramp pins RSI at 100 — struck as overbought."""
    row = strategy.evaluate_technical(
        [RankedName("HOT", 1, "Auto")], {"HOT.NS": _ohlcv(np.linspace(100, 300, 200))})[0]
    assert row.struck and any("RSI" in s for s in row.strikes)


def test_technical_missing_history_is_unverified_and_passes():
    """The data-safeguard: an absent number is a flag, never a strike."""
    row = strategy.evaluate_technical([RankedName("NEW", 1, "Auto")], {})[0]
    assert row.struck is False
    assert len(row.flags) == 3          # all three checks unverified


def test_technical_thresholds_are_overridable():
    rng = np.random.default_rng(9)
    healthy = {"OK.NS": _ohlcv(100 * np.cumprod(1 + rng.normal(0.0012, 0.012, 300)))}
    name = [RankedName("OK", 1, "Auto")]
    assert strategy.evaluate_technical(name, healthy)[0].struck is False
    # An impossible RSI ceiling strikes everything.
    assert strategy.evaluate_technical(name, healthy, max_rsi=0.0)[0].struck is True


# --- caps -------------------------------------------------------------------

def _survivors(*specs):
    return [RankedName(sym, rank, sector) for sym, rank, sector in specs]


def test_caps_fill_open_slots_in_strict_rank_order():
    res = strategy.apply_caps(
        _survivors(("C", 3, "Auto"), ("A", 1, "IT"), ("B", 2, "Pharma")), [], set())
    assert [b.symbol for b in res.buys] == ["A", "B", "C"]


def test_caps_count_kept_holdings_against_the_sector_limit():
    held = [Position(f"H{i}", "Auto") for i in range(3)]      # sector already full
    res = strategy.apply_caps(_survivors(("NEW", 1, "Auto")), held, set())
    assert res.buys == []
    assert res.skipped[0].reason == "sector_cap"


def test_caps_limit_microcap_exposure():
    survivors = _survivors(*[(f"M{i}", i + 1, f"S{i}") for i in range(6)])
    micro = {f"M{i}" for i in range(6)}
    res = strategy.apply_caps(survivors, [], micro)
    assert len(res.buys) == config.MAX_MICROCAP
    assert all(s.reason == "microcap_cap" for s in res.skipped)


def test_caps_leave_unfilled_slots_in_cash():
    res = strategy.apply_caps(_survivors(("A", 1, "IT")), [], set())
    assert len(res.buys) == 1
    assert res.unfilled_slots == res.open_slots - 1     # never forced to fill


def test_caps_open_slots_shrink_with_the_portfolio():
    held = [Position(f"H{i}", f"S{i}") for i in range(config.TARGET_PORTFOLIO_MAX)]
    res = strategy.apply_caps(_survivors(("A", 1, "IT")), held, set())
    assert res.open_slots == 0 and res.buys == []
    assert res.skipped[0].reason == "slots_full"


def test_microcap_membership_overrides_the_ledger_sleeve():
    res = strategy.apply_caps([], [Position("X", "Auto", "MIDSMALLCAP400")], {"X"})
    assert res.kept[0].sleeve == "MICROCAP250"
    assert res.microcap_count == 1
