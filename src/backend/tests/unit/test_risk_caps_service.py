"""Step-6 risk caps & slot-filling tests (Spec v2.2 §2.8 + §2.10)."""

import numpy as np
import pandas as pd
import pytest

from app import config
from app.schemas.common import StepStatus
from app.schemas.fundamental import PartDFundamentalRunRequest
from app.services import (
    cycle_service,
    fundamental_service,
    risk_caps_service,
    technical_service,
)
from app.services.errors import StepNotReady
from tests.unit.test_fundamental_service import CANDIDATES, at_step4  # noqa: F401
from tests.unit.test_scanner_service import _ohlcv


@pytest.fixture
def at_step6(at_step4, monkeypatch, now_ist):  # noqa: F811
    """Drive through Steps 4 and 5 so the risk caps have survivors to work with."""
    fundamental_service.run(PartDFundamentalRunRequest(), now_ist=now_ist)
    cycle_service.advance_part_d_fundamental(now=now_ist)
    # A gentle uptrend WITH noise: above the 50-DMA, near the 20-DMA and RSI
    # mid-range, so names actually survive Step 5. (A monotonic ramp would give
    # RSI 100 and strike everything as overbought.)
    rng = np.random.default_rng(7)
    healthy = _ohlcv(100 * np.cumprod(1 + rng.normal(0.0012, 0.012, 300)))
    monkeypatch.setattr(
        technical_service.stock_prices, "get_prices",
        lambda tickers, as_of=None, today=None: {t: healthy for t in tickers})
    technical_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_technical(now=now_ist)
    # No microcap membership unless a test opts in.
    monkeypatch.setattr(risk_caps_service, "_microcap_symbols", lambda: set())
    return at_step4


def _out(now_ist):
    return risk_caps_service.run(now_ist=now_ist).steps.part_d_risk_caps.output


def test_requires_step6(at_step4, now_ist):  # noqa: F811
    with pytest.raises(StepNotReady):        # still at Step 4
        risk_caps_service.run(now_ist=now_ist)


def test_open_slots_account_for_kept_holdings(at_step6, now_ist):
    out = _out(now_ist)
    # The fixture seeds 1 ACTIVE holding (SANSERA).
    assert len(out.kept) == 1
    assert out.open_slots == config.TARGET_PORTFOLIO_MAX - 1
    assert out.final_portfolio_size == len(out.kept) + len(out.buys)


def test_buys_are_taken_in_strict_rank_order(at_step6, now_ist):
    out = _out(now_ist)
    ranks = [b.rank for b in out.buys]
    assert ranks == sorted(ranks)


def test_sector_cap_skips_the_fourth_name_in_a_sector(at_step6, monkeypatch, now_ist):
    monkeypatch.setattr(config, "MAX_PER_SECTOR", 1)
    out = _out(now_ist)

    # ALPHA is "Auto Components" — same sector as the kept SANSERA, so with a
    # cap of 1 it must be skipped rather than bought.
    skipped = {s.symbol: s for s in out.skipped}
    assert "ALPHA" in skipped and skipped["ALPHA"].reason == "sector_cap"
    assert "ALPHA" not in {b.symbol for b in out.buys}
    # A cap breach must not stop the loop — later ranks still get bought.
    assert out.buys, "loop should continue past a capped name"


def test_sector_counts_never_exceed_the_cap(at_step6, now_ist):
    out = _out(now_ist)
    assert all(n <= config.MAX_PER_SECTOR for n in out.sector_counts.values())


def test_microcap_cap_enforced(at_step6, monkeypatch, now_ist):
    monkeypatch.setattr(config, "MAX_MICROCAP", 1)
    monkeypatch.setattr(risk_caps_service, "_microcap_symbols",
                        lambda: {"ALPHA", "BETA", "GAMMA", "DELTA"})
    out = _out(now_ist)

    micro_buys = [b for b in out.buys if b.sleeve == "MICROCAP250"]
    assert len(micro_buys) <= 1
    assert any(s.reason == "microcap_cap" for s in out.skipped)


def test_slots_full_stops_buying(at_step6, monkeypatch, now_ist):
    monkeypatch.setattr(config, "TARGET_PORTFOLIO_MAX", 2)   # 1 kept -> 1 open slot
    out = _out(now_ist)

    assert out.open_slots == 1
    assert len(out.buys) == 1
    assert any(s.reason == "slots_full" for s in out.skipped)


def test_unfilled_slots_stay_in_cash(at_step6, monkeypatch, now_ist):
    """Too few candidates must leave cash, never force a fill."""
    monkeypatch.setattr(config, "MAX_PER_SECTOR", 0)   # nothing can be bought
    out = _out(now_ist)

    assert out.buys == []
    assert out.unfilled_slots == out.open_slots
    assert any("CASH" in a for a in out.actions)


def test_advance_moves_to_journal(at_step6, now_ist):
    cyc = risk_caps_service.run(now_ist=now_ist)
    assert cyc.steps.part_d_risk_caps.status == StepStatus.DONE
    nxt = cycle_service.advance_part_d_risk_caps(now=now_ist)
    assert nxt.current_step == "part_e_journal"
