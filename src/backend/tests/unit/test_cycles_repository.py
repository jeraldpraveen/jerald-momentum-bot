"""Cycle repository + one-active-cycle enforcement."""

import datetime as dt

import pytest

from app import config
from app.repositories import cycles_repository as repo
from app.services import cycle_service
from app.services.errors import CycleConflict, NoActiveCycle


def test_no_active_cycle_raises(isolated):
    with pytest.raises(NoActiveCycle):
        cycle_service.get_current_cycle()


def test_create_cycle_sets_pointer_and_persists(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    assert cyc.status == "open"
    assert cyc.current_step == "part_a_regime_gate"
    assert repo.get_current_cycle_id() == cyc.cycle_id
    assert repo.load(cyc.cycle_id) is not None


def test_second_open_cycle_conflicts(isolated, now_ist):
    cycle_service.create_cycle(now=now_ist)
    with pytest.raises(CycleConflict) as exc:
        cycle_service.create_cycle(now=now_ist)
    assert exc.value.code == "cycle_already_open"
    assert exc.value.http_status == 409


def test_new_cycle_allowed_after_close(isolated, now_ist):
    import datetime as dt

    first = cycle_service.create_cycle(week_end=dt.date(2026, 7, 10), now=now_ist)
    first.status = "closed"
    repo.save(first)
    second = cycle_service.create_cycle(week_end=dt.date(2026, 7, 17), now=now_ist)
    assert second.cycle_id == "2026-07-17"


def test_same_week_restart_gets_unique_id_and_keeps_history(isolated, now_ist):
    first = cycle_service.create_cycle(now=now_ist)          # 2026-07-17
    cycle_service.close_current_cycle(now=now_ist)
    second = cycle_service.create_cycle(now=now_ist)         # same week -> suffixed

    assert first.cycle_id == "2026-07-17"
    assert second.cycle_id == "2026-07-17.2"
    assert repo.exists("2026-07-17")                          # prior kept as history
    assert repo.get_current_cycle_id() == "2026-07-17.2"


def test_delete_current_cycle_removes_file_and_pointer(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    assert repo.exists(cyc.cycle_id)

    deleted, journal_removed = cycle_service.delete_current_cycle()

    assert deleted == cyc.cycle_id
    assert journal_removed == 0                   # nothing was ever journalled
    assert not repo.exists(cyc.cycle_id)          # file gone (no history kept)
    assert repo.get_current_cycle_id() is None
    # A new cycle can reuse the id now that the file is gone.
    assert cycle_service.create_cycle(now=now_ist).cycle_id == cyc.cycle_id


def test_previous_regime_reads_prior_cycle(isolated, now_ist):
    from app.schemas.regime_gate import RegimeGateOutput

    prior = cycle_service.create_cycle(week_end=__import__("datetime").date(2026, 7, 10), now=now_ist)
    prior.steps.part_a_regime_gate.output = RegimeGateOutput(
        regime="RED", week_end="2026-07-10", index_close=1.0,
        line_label="Supertrend", line_value=2.0,
        index_id="midsmall400", index_label="NIFTY MIDSMALLCAP 400", index_ticker="MIDSMALL.NS",
        filter_id="st_1_2_5", filter_label="Supertrend (1, 2.5)", indicator="supertrend",
        data_source="test",
    )
    prior.status = "closed"
    repo.save(prior)

    assert repo.get_previous_regime(before_cycle_id="2026-07-17") == "RED"


def test_list_cycles_skips_an_unreadable_file(isolated, now_ist):
    """One incompatible cycle file must not blank out the whole history list."""
    cycle_service.create_cycle(week_end=dt.date(2026, 7, 17), now=now_ist)
    (config.CYCLES_DIR / "2026-07-10.json").write_text("{ not json", encoding="utf-8")

    ids = [s.cycle_id for s in repo.list_cycles()]
    assert ids == ["2026-07-17"]
