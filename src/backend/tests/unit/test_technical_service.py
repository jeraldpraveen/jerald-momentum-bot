"""Step-5 technical veto tests (Spec v2.2 checks 3/4/5)."""

import numpy as np
import pandas as pd
import pytest

from app import config
from app.domain import indicators
from app.schemas.common import StepStatus
from app.schemas.fundamental import FundamentalAssessment, PartDFundamentalRunRequest
from app.services import cycle_service, fundamental_service, technical_service
from app.services.errors import StepNotReady
from tests.unit.test_fundamental_service import CANDIDATES, at_step4  # noqa: F401
from tests.unit.test_scanner_service import _ohlcv


# --- indicator maths --------------------------------------------------------

def test_sma_matches_mean():
    s = pd.Series(np.arange(1, 101, dtype=float))
    assert indicators.sma(s, 50) == pytest.approx(s.iloc[-50:].mean())


def test_sma_none_when_short():
    assert indicators.sma(pd.Series([1.0, 2.0]), 50) is None


def test_rsi_is_100_when_only_gains():
    s = pd.Series(np.arange(1, 60, dtype=float))     # monotonic rise
    assert indicators.rsi(s, 14) == 100.0


def test_rsi_low_when_only_losses():
    s = pd.Series(np.arange(60, 1, -1, dtype=float))  # monotonic fall
    assert indicators.rsi(s, 14) == pytest.approx(0.0, abs=1e-6)


def test_rsi_mid_range_for_choppy_series():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    val = indicators.rsi(s, 14)
    assert 0 < val < 100


def test_rsi_none_when_short():
    assert indicators.rsi(pd.Series([1.0, 2.0, 3.0]), 14) is None


# --- service ----------------------------------------------------------------

def _to_step5(now_ist, assessments=None):
    fundamental_service.run(
        PartDFundamentalRunRequest(assessments=assessments or {}), now_ist=now_ist)
    return cycle_service.advance_part_d_fundamental(now=now_ist)


def _patch_tech_prices(monkeypatch, mapping):
    monkeypatch.setattr(technical_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None:
                        {t: mapping[t] for t in tickers if t in mapping})


def test_requires_step5(at_step4, now_ist):  # noqa: F811
    with pytest.raises(StepNotReady):        # still at Step 4
        technical_service.run(now_ist=now_ist)


def test_only_step4_survivors_are_evaluated(at_step4, monkeypatch, now_ist):  # noqa: F811
    # Strike ALPHA on fundamentals; it must not appear in Step 5.
    _to_step5(now_ist, {"ALPHA": FundamentalAssessment(pledging_pct=99.0)})
    _patch_tech_prices(monkeypatch, {
        f"{s}.NS": _ohlcv(np.linspace(100, 200, 300)) for s, _ in CANDIDATES})

    out = technical_service.run(now_ist=now_ist).steps.part_d_technical.output
    symbols = {c.symbol for c in out.candidates}
    assert "ALPHA" not in symbols
    assert symbols == {s for s, _ in CANDIDATES} - {"ALPHA"}


def test_price_below_50dma_strikes(at_step4, monkeypatch, now_ist):  # noqa: F811
    _to_step5(now_ist)
    # Rise then sharp fall -> last close below the 50-DMA.
    falling = _ohlcv(list(np.linspace(100, 200, 260)) + list(np.linspace(200, 120, 40)))
    healthy = _ohlcv(np.linspace(100, 200, 300))
    _patch_tech_prices(monkeypatch, {
        "ALPHA.NS": falling,
        **{f"{s}.NS": healthy for s, _ in CANDIDATES if s != "ALPHA"}})

    out = technical_service.run(now_ist=now_ist).steps.part_d_technical.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.trend.verdict == "strike" and alpha.struck
    assert alpha.close < alpha.dma_50


def test_over_extended_above_20dma_strikes(at_step4, monkeypatch, now_ist):  # noqa: F811
    _to_step5(now_ist)
    # Flat, then a violent spike -> far above the 20-DMA.
    spike = _ohlcv(list(np.full(280, 100.0)) + list(np.linspace(100, 400, 20)))
    _patch_tech_prices(monkeypatch, {"ALPHA.NS": spike})

    out = technical_service.run(now_ist=now_ist).steps.part_d_technical.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.extension_pct > config.MAX_EXTENSION_PCT
    assert alpha.extension.verdict == "strike" and alpha.struck


def test_overbought_rsi_strikes(at_step4, monkeypatch, now_ist):  # noqa: F811
    _to_step5(now_ist)
    relentless = _ohlcv(np.linspace(100, 300, 300))   # only gains -> RSI 100
    _patch_tech_prices(monkeypatch, {"ALPHA.NS": relentless})

    out = technical_service.run(now_ist=now_ist).steps.part_d_technical.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.rsi_14 > config.MAX_RSI
    assert alpha.overbought.verdict == "strike" and alpha.struck


def test_healthy_uptrend_passes_all_three(at_step4, monkeypatch, now_ist):  # noqa: F811
    _to_step5(now_ist)
    rng = np.random.default_rng(3)
    # Gentle drift with noise: above 50-DMA, near 20-DMA, RSI mid-range.
    path = 100 * np.cumprod(1 + rng.normal(0.0012, 0.012, 300))
    _patch_tech_prices(monkeypatch, {"ALPHA.NS": _ohlcv(path)})

    out = technical_service.run(now_ist=now_ist).steps.part_d_technical.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert not alpha.struck
    assert alpha.trend.verdict == "pass"
    assert alpha.extension.verdict == "pass"
    assert alpha.overbought.verdict == "pass"
    assert "ALPHA" in out.survivors


def test_missing_history_is_unverified_not_struck(at_step4, monkeypatch, now_ist):  # noqa: F811
    _to_step5(now_ist)
    _patch_tech_prices(monkeypatch, {"ALPHA.NS": _ohlcv(np.linspace(100, 110, 5))})

    out = technical_service.run(now_ist=now_ist).steps.part_d_technical.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.trend.verdict == "unverified"
    assert not alpha.struck and alpha.flags


def test_advance_moves_to_risk_caps(at_step4, monkeypatch, now_ist):  # noqa: F811
    _to_step5(now_ist)
    _patch_tech_prices(monkeypatch, {
        f"{s}.NS": _ohlcv(np.linspace(100, 200, 300)) for s, _ in CANDIDATES})
    cyc = technical_service.run(now_ist=now_ist)
    assert cyc.steps.part_d_technical.status == StepStatus.DONE

    nxt = cycle_service.advance_part_d_technical(now=now_ist)
    assert nxt.current_step == "part_d_risk_caps"
