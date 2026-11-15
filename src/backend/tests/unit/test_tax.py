"""Capital-gains tax model — the rules that decide whether the edge survives.

Each test pins one rule of the Indian set-off ladder. They are spelled out as
arithmetic on purpose: a tax model that is "roughly right" moves the after-tax
CAGR by more than the edge it is meant to measure.
"""

import datetime as dt

import pytest

from app.backtest import tax
from app.backtest.tax import CarriedLoss, TaxLedger, TaxRules

RULES = TaxRules()
SHORT, LONG = 100, 400            # holding periods either side of 365 days


def d(y, m, day=15):
    return dt.date(y, m, day)


# --- financial year ---------------------------------------------------------

@pytest.mark.parametrize("date,fy", [
    (dt.date(2024, 4, 1), 2024),      # first day of the year
    (dt.date(2025, 3, 31), 2024),     # last day of the same year
    (dt.date(2025, 1, 10), 2024),     # January belongs to the year that began in April
    (dt.date(2024, 3, 31), 2023),
])
def test_financial_year_runs_april_to_march(date, fy):
    assert tax.financial_year(date) == fy


def test_fy_label_is_the_two_year_form():
    assert tax.fy_label(2024) == "2024-25"
    assert tax.fy_label(1999) == "1999-00"


def test_rules_serialise_every_parameter():
    assert RULES.as_dict() == {
        "stcg_pct": 20.0, "ltcg_pct": 12.5, "ltcg_exemption": 125_000.0,
        "long_term_days": 365, "carry_forward_years": 8, "cash_income_pct": 30.0,
    }


# --- set-off ----------------------------------------------------------------

def test_short_term_loss_absorbs_long_term_gain_in_the_same_year():
    st, lt, left = tax.set_off(-300.0, 1000.0, [])
    assert (st, lt, left) == (0.0, 700.0, [])


def test_short_term_loss_larger_than_the_long_term_gain_leaves_a_loss():
    st, lt, _ = tax.set_off(-1000.0, 300.0, [])
    assert (st, lt) == (-700.0, 0.0)


def test_long_term_loss_never_shelters_short_term_gain():
    st, lt, _ = tax.set_off(1000.0, -400.0, [])
    assert (st, lt) == (1000.0, -400.0)


def test_carried_short_loss_hits_short_gains_first_then_long():
    st, lt, left = tax.set_off(100.0, 500.0, [CarriedLoss(2020, short=300.0)])
    assert (st, lt) == (0.0, 300.0)                 # 100 off short, 200 off long
    assert left == [CarriedLoss(2020, 0.0, 0.0)]


def test_carried_long_loss_only_reduces_long_gains():
    st, lt, left = tax.set_off(100.0, 500.0, [CarriedLoss(2020, long=200.0)])
    assert (st, lt) == (100.0, 300.0)
    assert left == [CarriedLoss(2020, 0.0, 0.0)]


def test_unused_carried_loss_is_returned_intact_and_input_is_not_mutated():
    carried = [CarriedLoss(2020, short=300.0, long=50.0)]
    st, lt, left = tax.set_off(0.0, 0.0, carried)
    assert (st, lt) == (0.0, 0.0)
    assert left == [CarriedLoss(2020, 300.0, 50.0)]
    assert carried == [CarriedLoss(2020, 300.0, 50.0)]


def test_oldest_carried_loss_is_consumed_first():
    carried = [CarriedLoss(2019, short=100.0), CarriedLoss(2021, short=100.0)]
    _, _, left = tax.set_off(150.0, 0.0, carried)
    assert left == [CarriedLoss(2019, 0.0, 0.0), CarriedLoss(2021, 50.0, 0.0)]


def test_carried_loss_emptiness():
    assert CarriedLoss(2020).empty
    assert not CarriedLoss(2020, short=1.0).empty
    assert not CarriedLoss(2020, long=1.0).empty


# --- liability --------------------------------------------------------------

def test_short_term_gain_is_taxed_at_twenty_percent():
    assert tax.liability(100_000.0, 0.0, [], RULES) == pytest.approx(20_000.0)


def test_long_term_gain_is_taxed_only_above_the_exemption():
    assert tax.liability(0.0, 100_000.0, [], RULES) == 0.0
    assert tax.liability(0.0, 225_000.0, [], RULES) == pytest.approx(12_500.0)


def test_a_net_loss_owes_nothing():
    assert tax.liability(-50_000.0, -10_000.0, [], RULES) == 0.0


def test_carried_losses_are_set_off_before_the_exemption():
    """The unfavourable ordering the law actually uses: a brought-forward loss
    is consumed against a gain the exemption would have covered anyway."""
    owed = tax.liability(0.0, 325_000.0, [CarriedLoss(2020, long=100_000.0)], RULES)
    assert owed == pytest.approx((325_000 - 100_000 - 125_000) * 0.125)


# --- buy-and-hold exit ------------------------------------------------------

def test_exit_tax_long_term_uses_rate_and_exemption():
    assert tax.exit_tax(1_125_000.0, held_days=3650, rules=RULES) == pytest.approx(125_000.0)


def test_exit_tax_short_term_has_no_exemption():
    assert tax.exit_tax(100_000.0, held_days=200, rules=RULES) == pytest.approx(20_000.0)


def test_exit_tax_on_a_loss_is_zero():
    assert tax.exit_tax(-5.0, held_days=3650, rules=RULES) == 0.0
    assert tax.exit_tax(0.0, held_days=10, rules=RULES) == 0.0


# --- the ledger -------------------------------------------------------------

def test_each_sale_pays_the_change_in_year_to_date_liability():
    led = TaxLedger()
    assert led.realise(d(2024, 5), 100_000.0, SHORT) == pytest.approx(20_000.0)
    assert led.realise(d(2024, 6), 50_000.0, SHORT) == pytest.approx(10_000.0)
    assert led.total_paid == pytest.approx(30_000.0)


def test_a_loss_after_gains_refunds_tax_already_paid():
    led = TaxLedger()
    led.realise(d(2024, 5), 100_000.0, SHORT)
    refund = led.realise(d(2024, 9), -40_000.0, SHORT)
    assert refund == pytest.approx(-8_000.0)
    assert led.capital_gains_tax == pytest.approx(12_000.0)


def test_a_loss_larger_than_the_years_gains_refunds_only_what_was_paid():
    led = TaxLedger()
    led.realise(d(2024, 5), 10_000.0, SHORT)
    assert led.realise(d(2024, 6), -50_000.0, SHORT) == pytest.approx(-2_000.0)
    assert led.capital_gains_tax == 0.0


def test_holding_period_decides_the_bucket():
    led = TaxLedger()
    led.realise(d(2024, 5), 200_000.0, SHORT)
    led.realise(d(2024, 5), 200_000.0, LONG)
    y = led.years[2024]
    assert (y.short, y.long) == (200_000.0, 200_000.0)
    assert led.capital_gains_tax == pytest.approx(40_000 + (200_000 - 125_000) * 0.125)


def test_exactly_365_days_is_still_short_term():
    led = TaxLedger()
    led.realise(d(2024, 5), 1000.0, 365)
    led.realise(d(2024, 5), 1000.0, 366)
    assert (led.years[2024].short, led.years[2024].long) == (1000.0, 1000.0)


def test_a_new_financial_year_starts_from_zero():
    led = TaxLedger()
    led.realise(d(2024, 5), 100_000.0, SHORT)
    paid = led.realise(d(2025, 5), 100_000.0, SHORT)
    assert paid == pytest.approx(20_000.0)          # not 40,000 minus 20,000
    assert sorted(led.years) == [2024, 2025]


def test_a_net_loss_year_carries_forward_and_shelters_the_next():
    led = TaxLedger()
    led.realise(d(2024, 5), -100_000.0, SHORT)
    paid = led.realise(d(2025, 5), 150_000.0, SHORT)
    assert paid == pytest.approx(10_000.0)          # only 50,000 is taxable
    assert led.carried == [CarriedLoss(2024, short=100_000.0)]


def test_a_carried_loss_is_consumed_when_the_year_closes():
    led = TaxLedger()
    led.realise(d(2024, 5), -100_000.0, SHORT)
    led.realise(d(2025, 5), 150_000.0, SHORT)
    led.realise(d(2026, 5), 10_000.0, SHORT)        # rolls FY2025 shut
    assert led.carried == []
    assert led.years[2026].capital_gains_tax == pytest.approx(2_000.0)


def test_a_long_term_loss_carries_forward_separately():
    led = TaxLedger()
    led.realise(d(2024, 5), -80_000.0, LONG)
    led.realise(d(2025, 5), 50_000.0, SHORT)        # cannot use the long-term loss
    assert led.years[2025].capital_gains_tax == pytest.approx(10_000.0)
    assert led.carried == [CarriedLoss(2024, long=80_000.0)]


def test_carried_losses_expire_after_eight_years():
    led = TaxLedger()
    led.realise(d(2010, 5), -100_000.0, SHORT)
    led.realise(d(2018, 5), 0.0, SHORT)             # eighth year: still alive
    assert led.carried == [CarriedLoss(2010, short=100_000.0)]
    paid = led.realise(d(2019, 5), 100_000.0, SHORT)   # ninth year: gone
    assert led.carried == []
    assert paid == pytest.approx(20_000.0)


def test_years_with_no_activity_can_be_skipped():
    led = TaxLedger()
    led.realise(d(2020, 5), -30_000.0, SHORT)
    paid = led.realise(d(2023, 5), 30_000.0, SHORT)
    assert paid == 0.0                              # fully sheltered
    assert sorted(led.years) == [2020, 2023]


def test_an_earlier_date_never_reopens_a_closed_year():
    led = TaxLedger()
    led.realise(d(2025, 5), 10_000.0, SHORT)
    led.realise(d(2024, 5), 10_000.0, SHORT)        # out of order: stays in FY2025
    assert sorted(led.years) == [2025]
    assert led.years[2025].short == 20_000.0


def test_cash_income_is_taxed_at_the_slab_rate():
    led = TaxLedger()
    assert led.cash_income(d(2024, 5), 1_000.0) == pytest.approx(300.0)
    assert led.cash_income_tax == pytest.approx(300.0)
    assert led.total_paid == pytest.approx(300.0)


def test_non_positive_cash_income_is_ignored():
    led = TaxLedger()
    assert led.cash_income(d(2024, 5), 0.0) == 0.0
    assert led.cash_income(d(2024, 5), -10.0) == 0.0
    assert led.years == {}


def test_custom_rules_are_honoured():
    led = TaxLedger(rules=TaxRules(stcg_pct=15.0, cash_income_pct=10.0))
    assert led.realise(d(2024, 5), 100_000.0, SHORT) == pytest.approx(15_000.0)
    assert led.cash_income(d(2024, 5), 1_000.0) == pytest.approx(100.0)


def test_losses_carried_counts_closed_and_open_years():
    led = TaxLedger()
    assert led.losses_carried() == 0.0              # nothing booked yet
    led.realise(d(2024, 5), -100_000.0, SHORT)
    assert led.losses_carried() == pytest.approx(100_000.0)     # open year's loss
    led.realise(d(2025, 5), -20_000.0, LONG)
    assert led.losses_carried() == pytest.approx(120_000.0)     # carried + open


def test_summary_reports_every_year_in_order():
    led = TaxLedger()
    led.realise(d(2025, 5), 100_000.0, SHORT)
    led.cash_income(d(2025, 6), 1_000.0)
    led.realise(d(2026, 5), -10_000.0, LONG)
    s = led.summary()

    assert s["total_tax_paid"] == pytest.approx(20_300.0)
    assert s["capital_gains_tax"] == pytest.approx(20_000.0)
    assert s["cash_income_tax"] == pytest.approx(300.0)
    assert s["losses_carried_forward"] == pytest.approx(10_000.0)
    assert s["rules"]["stcg_pct"] == 20.0
    assert [y["fy"] for y in s["by_year"]] == ["2025-26", "2026-27"]
    assert s["by_year"][0] == {
        "fy": "2025-26", "short_term_gain": 100_000.0, "long_term_gain": 0.0,
        "capital_gains_tax": 20_000.0, "cash_income_tax": 300.0,
    }
