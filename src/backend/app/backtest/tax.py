"""Capital-gains tax for the backtest — Indian listed-equity rules.

A pre-tax equity curve answers the wrong question. This strategy turns the
portfolio over about six times a year, so nearly every gain is SHORT-term and
taxed at the higher rate, while the index fund it is compared with defers a
lower LONG-term rate until the day it is sold. Leaving tax out flatters the
strategy by several points a year — which is the whole margin in dispute.

The model, deliberately simple and stated with every run:

  * Financial year runs 1 April - 31 March.
  * Held MORE than `long_term_days` -> long-term, otherwise short-term.
  * Within a year a short-term loss absorbs long-term gains; a long-term loss
    can only absorb long-term gains.
  * Net losses carry forward `carry_forward_years` years under the same rule.
  * Long-term gains are taxed only above the yearly exemption.
  * Income on parked cash (a liquid fund) is taxed at the slab rate.

Tax is settled AS IT ACCRUES: each sale pays (or refunds) the change in the
year-to-date liability. That is slightly early versus quarterly advance tax,
and it keeps the simulated account from spending money it already owes.

TODAY'S rates are applied to all of history. Past rates were lower, but the
question being answered is "is this worth running from here", not "what would
the tax office have collected in 2009".

Pure: no I/O, no pandas — just arithmetic on dates and rupees.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

FY_START_MONTH = 4      # April


@dataclass(frozen=True)
class TaxRules:
    stcg_pct: float = 20.0              # short-term capital gains, listed equity
    ltcg_pct: float = 12.5              # long-term capital gains, listed equity
    ltcg_exemption: float = 125_000.0   # per financial year
    long_term_days: int = 365           # held MORE than this -> long-term
    carry_forward_years: int = 8
    cash_income_pct: float = 30.0       # slab rate on liquid-fund income

    def as_dict(self) -> dict:
        return {
            "stcg_pct": self.stcg_pct, "ltcg_pct": self.ltcg_pct,
            "ltcg_exemption": self.ltcg_exemption,
            "long_term_days": self.long_term_days,
            "carry_forward_years": self.carry_forward_years,
            "cash_income_pct": self.cash_income_pct,
        }


def financial_year(d: dt.date) -> int:
    """The calendar year the financial year STARTS in (FY 2024 = Apr-24..Mar-25)."""
    return d.year if d.month >= FY_START_MONTH else d.year - 1


def fy_label(fy: int) -> str:
    """2024 -> '2024-25'."""
    return f"{fy}-{(fy + 1) % 100:02d}"


@dataclass
class CarriedLoss:
    """A prior year's unabsorbed loss, kept split because the two kinds have
    different set-off rights."""

    fy: int
    short: float = 0.0
    long: float = 0.0

    @property
    def empty(self) -> bool:
        return self.short <= 0 and self.long <= 0


def set_off(short: float, long: float,
            carried: list[CarriedLoss]) -> tuple[float, float, list[CarriedLoss]]:
    """Apply the set-off rules to one year's net gains.

    Returns (short after set-off, long after set-off, carried losses left).
    A negative result is that year's own unabsorbed loss. The `carried` list is
    not mutated — the caller decides whether the consumption is final.
    """
    # This year's short-term loss may absorb this year's long-term gain. The
    # reverse is not allowed: a long-term loss never shelters short-term gains.
    if short < 0 < long:
        used = min(-short, long)
        short += used
        long -= used

    left: list[CarriedLoss] = []
    for loss in carried:                                  # oldest first
        s, l = loss.short, loss.long
        if s > 0 and short > 0:
            used = min(s, short)
            s -= used
            short -= used
        if s > 0 and long > 0:
            used = min(s, long)
            s -= used
            long -= used
        if l > 0 and long > 0:
            used = min(l, long)
            l -= used
            long -= used
        left.append(CarriedLoss(loss.fy, s, l))
    return short, long, left


def liability(short: float, long: float, carried: list[CarriedLoss],
              rules: TaxRules) -> float:
    """Capital-gains tax owed on a year's net gains so far."""
    st, lt, _ = set_off(short, long, carried)
    taxable_long = max(0.0, lt - rules.ltcg_exemption)
    return max(0.0, st) * rules.stcg_pct / 100 + taxable_long * rules.ltcg_pct / 100


def exit_tax(gain: float, held_days: int, rules: TaxRules) -> float:
    """Tax on selling ONE position held from day one — the buy-and-hold case."""
    if gain <= 0:
        return 0.0
    if held_days > rules.long_term_days:
        return max(0.0, gain - rules.ltcg_exemption) * rules.ltcg_pct / 100
    return gain * rules.stcg_pct / 100


@dataclass
class _Year:
    short: float = 0.0
    long: float = 0.0
    capital_gains_tax: float = 0.0
    cash_income_tax: float = 0.0


@dataclass
class TaxLedger:
    """Running tax position for one simulated account."""

    rules: TaxRules = field(default_factory=TaxRules)
    carried: list[CarriedLoss] = field(default_factory=list)
    years: dict[int, _Year] = field(default_factory=dict)
    _fy: int | None = None

    # --- internals ----------------------------------------------------------
    def _roll(self, on: dt.date) -> _Year:
        """Move to the financial year containing `on`, closing the old one."""
        fy = financial_year(on)
        if self._fy is not None and fy > self._fy:
            self._close(self._fy, fy)
        if self._fy is None or fy > self._fy:
            self._fy = fy
        return self.years.setdefault(self._fy, _Year())

    def _close(self, old: int, new: int) -> None:
        """Year end: consume the carried losses used and bank any new loss."""
        y = self.years.get(old, _Year())
        st, lt, left = set_off(y.short, y.long, self.carried)
        keep = [c for c in left
                if not c.empty and new - c.fy <= self.rules.carry_forward_years]
        if st < 0 or lt < 0:
            keep.append(CarriedLoss(old, short=max(0.0, -st), long=max(0.0, -lt)))
        self.carried = keep

    # --- events -------------------------------------------------------------
    def realise(self, on: dt.date, gain: float, held_days: int) -> float:
        """Book one closed position. Returns the cash to pay now.

        Negative means a refund: a loss that pulls the year-to-date liability
        back down below what earlier sales already paid.
        """
        y = self._roll(on)
        if held_days > self.rules.long_term_days:
            y.long += gain
        else:
            y.short += gain
        due = liability(y.short, y.long, self.carried, self.rules)
        delta = due - y.capital_gains_tax
        y.capital_gains_tax = due
        return delta

    def cash_income(self, on: dt.date, income: float) -> float:
        """Tax on income from parked cash. Returns the cash to pay now."""
        if income <= 0:
            return 0.0
        y = self._roll(on)
        tax = income * self.rules.cash_income_pct / 100
        y.cash_income_tax += tax
        return tax

    # --- reads --------------------------------------------------------------
    @property
    def capital_gains_tax(self) -> float:
        return sum(y.capital_gains_tax for y in self.years.values())

    @property
    def cash_income_tax(self) -> float:
        return sum(y.cash_income_tax for y in self.years.values())

    @property
    def total_paid(self) -> float:
        return self.capital_gains_tax + self.cash_income_tax

    def losses_carried(self) -> float:
        """Unabsorbed losses still available, including the open year's."""
        if self._fy is None:
            return 0.0
        y = self.years[self._fy]
        st, lt, left = set_off(y.short, y.long, self.carried)
        return (sum(c.short + c.long for c in left)
                + max(0.0, -st) + max(0.0, -lt))

    def summary(self) -> dict:
        return {
            "rules": self.rules.as_dict(),
            "total_tax_paid": round(self.total_paid, 2),
            "capital_gains_tax": round(self.capital_gains_tax, 2),
            "cash_income_tax": round(self.cash_income_tax, 2),
            "losses_carried_forward": round(self.losses_carried(), 2),
            "by_year": [
                {"fy": fy_label(fy),
                 "short_term_gain": round(y.short, 2),
                 "long_term_gain": round(y.long, 2),
                 "capital_gains_tax": round(y.capital_gains_tax, 2),
                 "cash_income_tax": round(y.cash_income_tax, 2)}
                for fy, y in sorted(self.years.items())
            ],
        }
