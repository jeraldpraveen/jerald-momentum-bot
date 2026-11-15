"""Part C portfolio-audit tests: rank exits, dropped names, preview vs commit."""

import numpy as np
import pandas as pd
import pytest

from app import config
from app.schemas.common import StepStatus
from app.schemas.scanner import PartBRunRequest
from app.services import cycle_service, exit_flags_service, regime_gate_service, scanner_service
from app.services.errors import StepNotReady
from tests.conftest import green_data
from tests.unit.test_scanner_service import HOLDINGS, _ohlcv, _patch_prices, _seed_holdings


@pytest.fixture
def at_part_c(isolated, monkeypatch, now_ist):
    """Cycle advanced through GREEN Part A and a Part B scoring run."""
    _seed_holdings(HOLDINGS)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)
    return isolated


def _run_scan(monkeypatch, now_ist, prices):
    _patch_prices(monkeypatch, prices)
    scanner_service.run(PartBRunRequest(universe_ids=["holdings"]), now_ist=now_ist)
    return cycle_service.advance_part_b(now=now_ist)


def _liquid(closes):
    return _ohlcv(closes)


def test_requires_part_c_step(at_part_c, now_ist):
    with pytest.raises(StepNotReady):          # still at Part B
        exit_flags_service.run(now_ist=now_ist)


def test_all_ranked_within_threshold_flags_nothing(at_part_c, monkeypatch, now_ist):
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400)) for s, _ in HOLDINGS}
    _run_scan(monkeypatch, now_ist, prices)
    cyc = exit_flags_service.run(now_ist=now_ist)
    out = cyc.steps.part_c_exit_flags.output

    assert cyc.steps.part_c_exit_flags.status == StepStatus.DONE
    assert out.exit_count == 0
    assert out.keep_count == len(HOLDINGS)
    assert all(not h.exit_flagged for h in out.holdings)


def test_a_holding_that_could_not_be_priced_is_kept_and_reported_as_not_audited(
        at_part_c, monkeypatch, now_ist):
    """A failed price download is a data gap, not an index deletion (Spec v2.3:
    a missing number is never a strike). SONACOMS has no price data this week."""
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400))
              for s, _ in HOLDINGS if s != "SONACOMS"}
    _run_scan(monkeypatch, now_ist, prices)
    cyc = exit_flags_service.run(now_ist=now_ist)
    out = cyc.steps.part_c_exit_flags.output

    gap = {h.symbol: h for h in out.holdings}["SONACOMS"]
    assert not gap.exit_flagged and gap.reasons == ["price_data_unavailable"]
    assert out.exit_count == 0 and out.keep_count == len(HOLDINGS)
    note = next(a for a in out.actions if a.startswith("NOT AUDITED"))
    assert "no current price for SONACOMS" in note and "Refresh prices" in note


def test_a_holding_outside_the_scanned_universe_is_flagged_dropped(at_part_c, monkeypatch,
                                                                  now_ist):
    """The real 'dropped from the index' case: the name is not in the scan at all."""
    monkeypatch.setattr(
        scanner_service, "_resolve_universes",
        lambda ids: [(f"{s}.NS", sec, "") for s, sec in HOLDINGS if s != "SONACOMS"])
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400)) for s, _ in HOLDINGS}
    _run_scan(monkeypatch, now_ist, prices)
    out = exit_flags_service.run(now_ist=now_ist).steps.part_c_exit_flags.output

    dropped = {h.symbol: h for h in out.holdings}["SONACOMS"]
    assert dropped.exit_flagged and dropped.in_universe is False
    assert dropped.reasons == ["dropped_from_universe"]
    assert out.exit_count == 1
    assert not any(a.startswith("NOT AUDITED") for a in out.actions)


def test_a_holding_outside_the_scan_filter_is_kept_and_reported_as_not_audited(
        at_part_c, monkeypatch, now_ist):
    from app.schemas.scanner import ScannerFilters

    _patch_prices(monkeypatch, {f"{s}.NS": _liquid(np.linspace(100, 300, 400))
                                for s, _ in HOLDINGS})
    scanner_service.run(PartBRunRequest(universe_ids=["holdings"],
                                        filters=ScannerFilters(sectors=["Banking"])),
                        now_ist=now_ist)
    cycle_service.advance_part_b(now=now_ist)
    out = exit_flags_service.run(now_ist=now_ist).steps.part_c_exit_flags.output

    assert out.exit_count == 0                      # a buy-side screen sells nothing
    screened = [h.symbol for h in out.holdings if h.reasons == ["excluded_by_scan_filter"]]
    assert sorted(screened) == sorted(s for s, sec in HOLDINGS if sec != "Banking")
    note = next(a for a in out.actions if a.startswith("NOT AUDITED"))
    assert "sector / price filter" in note and "TMB" not in note


def test_rank_beyond_threshold_flags_exit(at_part_c, monkeypatch, now_ist):
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400)) for s, _ in HOLDINGS}
    _run_scan(monkeypatch, now_ist, prices)
    monkeypatch.setattr(config, "RANK_EXIT_THRESHOLD", 2)   # only top-2 survive
    cyc = exit_flags_service.run(now_ist=now_ist)
    out = cyc.steps.part_c_exit_flags.output

    assert out.rank_threshold == 2
    assert out.exit_count == len(HOLDINGS) - 2
    for h in out.holdings:
        if h.rank and h.rank > 2:
            assert h.exit_flagged and "rank_exit" in h.reasons


def test_run_is_preview_advance_commits(at_part_c, monkeypatch, now_ist, isolated):
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400)) for s, _ in HOLDINGS}
    _run_scan(monkeypatch, now_ist, prices)
    monkeypatch.setattr(config, "RANK_EXIT_THRESHOLD", 2)
    exit_flags_service.run(now_ist=now_ist)

    # Preview: ledger untouched.
    before = pd.read_csv(isolated / "holdings_ledger.csv")
    assert (before["status"] == "ACTIVE").all()

    cyc = cycle_service.advance_part_c(now=now_ist)
    after = pd.read_csv(isolated / "holdings_ledger.csv")
    assert (after["status"] == "PENDING_EXIT").sum() == len(HOLDINGS) - 2
    assert (after["status"] == "ACTIVE").sum() == 2
    assert cyc.current_step == "part_d_fundamental"


def test_reopen_step_goes_back_and_resets_downstream(at_part_c, monkeypatch, now_ist):
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400)) for s, _ in HOLDINGS}
    _run_scan(monkeypatch, now_ist, prices)
    exit_flags_service.run(now_ist=now_ist)
    assert cycle_service.get_current_cycle().steps.part_c_exit_flags.status == StepStatus.DONE

    cyc = cycle_service.reopen_step("part_b_scanner", now=now_ist)

    assert cyc.current_step == "part_b_scanner"                    # back at Step 2
    assert cyc.steps.part_b_scanner.status == StepStatus.DONE      # its own result kept
    assert cyc.steps.part_c_exit_flags.status == StepStatus.PENDING  # downstream reset
    assert cyc.steps.part_c_exit_flags.output is None


def test_advance_before_run_raises(at_part_c, monkeypatch, now_ist):
    prices = {f"{s}.NS": _liquid(np.linspace(100, 300, 400)) for s, _ in HOLDINGS}
    _run_scan(monkeypatch, now_ist, prices)
    with pytest.raises(StepNotReady):
        cycle_service.advance_part_c(now=now_ist)
