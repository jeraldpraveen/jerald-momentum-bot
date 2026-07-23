"""Step-4 fundamental veto tests (Spec v2.2, eliminate-only)."""

import numpy as np
import pytest

from app import config
from app.schemas.common import StepStatus
from app.schemas.fundamental import FundamentalAssessment, PartDFundamentalRunRequest
from app.schemas.scanner import PartBRunRequest
from app.services import (
    cycle_service,
    exit_flags_service,
    fundamental_service,
    regime_gate_service,
    scanner_service,
)
from app.services.errors import StepNotReady
from tests.conftest import green_data
from tests.unit.test_scanner_service import _ohlcv, _patch_prices, _seed_holdings

# One held name (excluded from the veto pool) + several fresh candidates.
HELD = [("SANSERA", "Auto Components")]
CANDIDATES = [("ALPHA", "Auto Components"), ("BETA", "Banking"),
              ("GAMMA", "Healthcare"), ("DELTA", "Capital Goods")]


@pytest.fixture
def at_step4(isolated, monkeypatch, now_ist):
    """Drive a cycle to Step-4 with a known candidate pool."""
    _seed_holdings(HELD)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    # No auto interest coverage / no surveillance unless a test opts in.
    monkeypatch.setattr(fundamental_service.fundamentals, "get_interest_coverage",
                        lambda *a, **k: None)
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: {})

    # Universe = held name + candidates; all long/liquid so they rank.
    prices = {f"{s}.NS": _ohlcv(np.linspace(100, 100 + 40 * i, 400))
              for i, (s, _) in enumerate([*HELD, *CANDIDATES], start=1)}
    _patch_prices(monkeypatch, prices)
    monkeypatch.setattr(
        scanner_service, "_resolve_universes",
        lambda ids: [(f"{s}.NS", sec, "") for s, sec in [*HELD, *CANDIDATES]])

    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)
    scanner_service.run(PartBRunRequest(universe_ids=["holdings"]), now_ist=now_ist)
    cycle_service.advance_part_b(now=now_ist)
    exit_flags_service.run(now_ist=now_ist)
    cycle_service.advance_part_c(now=now_ist)
    return isolated


def _run(assessments=None, now_ist=None):
    return fundamental_service.run(
        PartDFundamentalRunRequest(assessments=assessments or {}), now_ist=now_ist)


def test_requires_step4(isolated, monkeypatch, now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(now=now_ist)
    with pytest.raises(StepNotReady):
        _run(now_ist=now_ist)


def test_pool_is_ranks_1_15_excluding_holdings(at_step4, now_ist):
    out = _run(now_ist=now_ist).steps.part_d_fundamental.output
    symbols = {c.symbol for c in out.candidates}
    assert "SANSERA" not in symbols                       # already held -> not re-vetoed
    assert symbols == {s for s, _ in CANDIDATES}
    assert out.rank_window == config.VETO_RANK_WINDOW
    assert all(c.rank <= config.VETO_RANK_WINDOW for c in out.candidates)


def test_blank_assessments_are_unverified_and_pass(at_step4, now_ist):
    """Data-safeguard: missing data never strikes — it passes with a flag."""
    out = _run(now_ist=now_ist).steps.part_d_fundamental.output
    for c in out.candidates:
        assert c.pledging.verdict == "unverified"
        assert c.pending_action.verdict == "unverified"
        assert not c.struck                # nothing struck on missing data
        assert c.flags                     # but flagged as unverified
    assert out.struck_count == 0


def test_pledging_above_threshold_strikes(at_step4, now_ist):
    out = _run({"ALPHA": FundamentalAssessment(pledging_pct=25.0)},
               now_ist=now_ist).steps.part_d_fundamental.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.pledging.verdict == "strike" and alpha.struck
    assert any("pledging" in s for s in alpha.strikes)


def test_pledging_within_threshold_passes(at_step4, now_ist):
    out = _run({"ALPHA": FundamentalAssessment(pledging_pct=5.0)},
               now_ist=now_ist).steps.part_d_fundamental.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.pledging.verdict == "pass" and not alpha.struck


def test_low_interest_coverage_strikes_non_financial(at_step4, now_ist):
    out = _run({"ALPHA": FundamentalAssessment(interest_coverage=1.2)},
               now_ist=now_ist).steps.part_d_fundamental.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.interest_cover.verdict == "strike" and alpha.struck


def test_interest_coverage_not_applicable_to_financials(at_step4, now_ist):
    """BETA is Banking — the IC check is non-financials only."""
    out = _run({"BETA": FundamentalAssessment(interest_coverage=0.1)},
               now_ist=now_ist).steps.part_d_fundamental.output
    beta = {c.symbol: c for c in out.candidates}["BETA"]
    assert beta.is_financial
    assert beta.interest_cover.verdict == "n/a"
    assert not beta.struck


def test_manual_overrides_auto_interest_coverage(at_step4, monkeypatch, now_ist):
    monkeypatch.setattr(fundamental_service.fundamentals, "get_interest_coverage",
                        lambda *a, **k: 50.0)                      # auto says healthy
    out = _run({"ALPHA": FundamentalAssessment(interest_coverage=1.0)},
               now_ist=now_ist).steps.part_d_fundamental.output     # user says otherwise
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.auto_interest_coverage == 50.0
    assert alpha.interest_cover.value == 1.0 and alpha.struck


def test_pending_action_strikes(at_step4, now_ist):
    out = _run({"GAMMA": FundamentalAssessment(pending_action=True)},
               now_ist=now_ist).steps.part_d_fundamental.output
    gamma = {c.symbol: c for c in out.candidates}["GAMMA"]
    assert gamma.pending_action.verdict == "strike" and gamma.struck


def test_nse_surveillance_auto_strikes(at_step4, monkeypatch, now_ist):
    """ASM/GSM listing is cited regulatory data -> automatic strike."""
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: {"DELTA": "NSE ASM (Stage I)"})
    out = _run(now_ist=now_ist).steps.part_d_fundamental.output
    by = {c.symbol: c for c in out.candidates}

    assert by["DELTA"].surveillance == "NSE ASM (Stage I)"
    assert by["DELTA"].pending_action.verdict == "strike" and by["DELTA"].struck
    assert "ASM" in by["DELTA"].pending_action.detail
    assert by["ALPHA"].pending_action.verdict == "unverified"   # others unaffected


def test_manual_none_cannot_override_surveillance(at_step4, monkeypatch, now_ist):
    """Published exchange data outranks a human 'none'."""
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: {"DELTA": "NSE GSM (Stage 0)"})
    out = _run({"DELTA": FundamentalAssessment(pending_action=False)},
               now_ist=now_ist).steps.part_d_fundamental.output
    delta = {c.symbol: c for c in out.candidates}["DELTA"]
    assert delta.pending_action.verdict == "strike" and delta.struck


def test_clean_name_marked_none_passes(at_step4, now_ist):
    out = _run({"ALPHA": FundamentalAssessment(pending_action=False)},
               now_ist=now_ist).steps.part_d_fundamental.output
    alpha = {c.symbol: c for c in out.candidates}["ALPHA"]
    assert alpha.pending_action.verdict == "pass" and not alpha.struck


def test_recent_listing_strikes_automatically(at_step4, monkeypatch, now_ist):
    monkeypatch.setattr(config, "MIN_LISTING_DAYS", 10_000)   # everything is "too new"
    out = _run(now_ist=now_ist).steps.part_d_fundamental.output
    assert all(c.listing_age.verdict == "strike" and c.struck for c in out.candidates)
    assert out.struck_count == len(out.candidates)


def test_assessments_persist_and_advance(at_step4, now_ist):
    cyc = _run({"ALPHA": FundamentalAssessment(pledging_pct=5.0, note="AR FY26")},
               now_ist=now_ist)
    assert cyc.steps.part_d_fundamental.status == StepStatus.DONE
    assert cyc.steps.part_d_fundamental.input["assessments"]["ALPHA"]["note"] == "AR FY26"

    nxt = cycle_service.advance_part_d_fundamental(now=now_ist)
    assert nxt.current_step == "part_d_technical"
