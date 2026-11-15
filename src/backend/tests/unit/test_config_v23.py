"""Spec v2.3 parameters — the ratified gate, cash parking, the promotion date.

These pin the decisions themselves. If one of them changes, a test should have
to change with it: they are Rulebook amendments, not tunables.
"""

import datetime as dt

import pytest

from app import config
from app.backtest import config as bt_config
from app.schemas.backtest import SweepVerdict


# --- amendment 2: the gate that was actually tested --------------------------

def test_the_default_gate_is_nifty_500_supertrend_1_2_5():
    index = config.get_index_option(None)
    filt = config.get_filter_option(None)

    assert (index.id, index.label, index.ticker, index.kind) == (
        "nifty500", "NIFTY 500", "^CRSLDX", "index")
    assert (filt.id, filt.params) == ("st_1_2_5", {"period": 1, "multiplier": 2.5})


def test_midsmallcap_400_stays_selectable_but_is_only_an_etf_proxy():
    opt = config.get_index_option("midsmall400")
    assert opt.kind == "etf_proxy" and opt.ticker == "MIDSMALL.NS"


@pytest.mark.parametrize("lookup,bad", [
    (config.get_index_option, "nope"),
    (config.get_filter_option, "nope"),
    (config.get_universe_option, "nope"),
    (config.get_alt_asset_option, "nope"),
])
def test_unknown_option_ids_raise(lookup, bad):
    with pytest.raises(KeyError, match="nope"):
        lookup(bad)


def test_no_alt_asset_id_means_no_alt_asset():
    assert config.get_alt_asset_option(None) is None
    assert config.get_alt_asset_option("") is None
    assert config.get_alt_asset_option("liquid").ticker == "LIQUIDBEES.NS"


def test_the_scan_universe_defaults_to_the_two_rulebook_indices():
    assert config.DEFAULT_UNIVERSE_IDS == ("midsmall400", "microcap250")
    labels = [config.get_universe_option(u).label for u in config.DEFAULT_UNIVERSE_IDS]
    assert labels == ["NIFTY MIDSMALLCAP 400", "NIFTY MICROCAP 250"]
    assert config.get_universe_option("holdings").source == "holdings"   # still selectable


# --- amendments 1 and 5: where idle cash goes, and where it does not ----------

def test_idle_cash_is_parked_in_a_liquid_or_overnight_fund():
    assert config.PARKING_INSTRUMENT == "liquid or overnight fund"


def test_gold_is_explicitly_not_a_parking_instrument():
    assert "gold" in config.PARKING_EXCLUDED and "gold_sbi" in config.PARKING_EXCLUDED
    for alt_id in config.PARKING_EXCLUDED:
        assert "liquid" not in config.get_alt_asset_option(alt_id).label.lower()
    for alt_id in ("gold", "gold_sbi"):
        note = config.get_alt_asset_option(alt_id).note
        assert "RESEARCH ONLY" in note and "not adopted" in note


def test_the_backtest_models_parked_cash_and_tax_by_default():
    assert bt_config.CASH_YIELD_PCT == 6.5
    assert bt_config.APPLY_TAX is True


# --- amendment 6: the promotion date ------------------------------------------

def test_shadow_ladder_restarted_on_the_2026_11_13_cycle():
    assert config.SHADOW_START == dt.date(2026, 11, 13)
    assert config.SHADOW_START.weekday() == 4                # a Friday
    assert config.SHADOW_WEEKS_REQUIRED == 12


def test_earliest_promotion_is_the_twelfth_friday():
    promo = config.earliest_promotion_date()
    assert promo == dt.date(2027, 1, 29)
    assert promo.weekday() == 4
    # Twelve Fridays inclusive of the first: eleven weeks later.
    assert (promo - config.SHADOW_START).days == 11 * 7


def test_a_missed_week_pushes_promotion_back():
    """Restarting the ladder a week later moves the date a week later."""
    later = config.SHADOW_START + dt.timedelta(weeks=1)
    assert config.earliest_promotion_date(start=later) == dt.date(2027, 2, 5)
    assert config.earliest_promotion_date(weeks=1) == config.SHADOW_START


# --- sweep verdict (schema property) ------------------------------------------

@pytest.mark.parametrize("percentile,helped", [(90.0, True), (95.0, True), (89.9, False)])
def test_selection_only_counts_as_helping_in_the_top_decile(percentile, helped):
    assert SweepVerdict(winner_test_percentile=percentile).selection_helped is helped
