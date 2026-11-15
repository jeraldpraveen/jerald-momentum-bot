"""Part B scanner-service tests: scoring, eligibility flags, ranking (mocked prices)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app import config
from app.schemas.common import StepStatus
from app.schemas.scanner import PartBRunRequest, ScannerFilters
from app.services import cycle_service, regime_gate_service, scanner_service
from app.domain import scoring_formula
from app.services.errors import FormulaFrozen, StepNotReady
from tests.conftest import green_data

# (symbol, sector) for the seeded holdings universe used by these tests.
HOLDINGS = [
    ("SANSERA", "Auto Components"), ("TMB", "Banking"),
    ("KIRLOSENG", "Industrial Machinery"), ("SONACOMS", "Auto Components"),
    ("ATHERENERG", "Auto"),
]


def _seed_holdings(rows):
    pd.DataFrame({
        "symbol": [s for s, _ in rows], "qty": "", "entry_price": 100,
        "entry_date": "", "stop_price": 80, "sleeve": "MIDSMALLCAP400",
        "sector": [sec for _, sec in rows], "status": "ACTIVE",
        "exit_reason": "", "marked_on": "",
    }).to_csv(config.HOLDINGS_LEDGER, index=False)


def _ohlcv(closes, volume=5_000_000, end=None):
    """Daily bars ENDING today by default — the scan refuses prices more than a
    week older than its as-of date, so test data has to be current too."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(end=end or dt.date.today(), periods=len(closes), freq="D")
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99,
         "close": closes, "volume": float(volume)},
        index=idx,
    )


@pytest.fixture
def at_part_b(isolated, monkeypatch, now_ist):
    """Advance a fresh cycle to Part B (GREEN Part A), holdings universe seeded."""
    _seed_holdings(HOLDINGS)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)
    return isolated


def _patch_prices(monkeypatch, mapping):
    monkeypatch.setattr(scanner_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {t: mapping[t] for t in tickers if t in mapping})


def test_run_requires_part_b_step(isolated, monkeypatch, now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(now=now_ist)  # still at Part A
    with pytest.raises(StepNotReady):
        scanner_service.run(PartBRunRequest(), now_ist=now_ist)


def test_scores_rank_and_flags(at_part_b, monkeypatch, now_ist):
    # Long, liquid, strongly-trending names score & rank; short/illiquid get flagged.
    strong = _ohlcv(np.linspace(100, 400, 400))
    weak = _ohlcv(np.linspace(100, 130, 400))
    illiquid = _ohlcv(np.linspace(100, 200, 400), volume=100)      # tiny turnover
    newbie = _ohlcv(np.linspace(100, 150, 50))                     # listed < 12m
    _patch_prices(monkeypatch, {
        "SANSERA.NS": strong, "TMB.NS": weak, "KIRLOSENG.NS": illiquid,
        "SONACOMS.NS": newbie, "ATHERENERG.NS": strong,
    })
    cyc = scanner_service.run(PartBRunRequest(universe_ids=["holdings"]), now_ist=now_ist)
    out = cyc.steps.part_b_scanner.output

    assert cyc.steps.part_b_scanner.status == StepStatus.DONE
    assert out.total == 5
    by_symbol = {c.symbol: c for c in out.candidates}
    assert "illiquid" in by_symbol["KIRLOSENG"].flags
    assert "listed<12m" in by_symbol["SONACOMS"].flags
    assert by_symbol["TMB"].eligible and by_symbol["TMB"].rank is not None
    # Eligible names carry sequential ranks starting at 1, score-ordered.
    eligible = [c for c in out.candidates if c.eligible]
    assert [c.rank for c in eligible] == list(range(1, len(eligible) + 1))
    scores = [c.score for c in eligible]
    assert scores == sorted(scores, reverse=True)


def test_sector_and_price_filters(at_part_b, monkeypatch, now_ist):
    prices = {t: _ohlcv(np.linspace(100, 300, 400)) for t in
              ["SANSERA.NS", "TMB.NS", "KIRLOSENG.NS", "SONACOMS.NS", "ATHERENERG.NS"]}
    _patch_prices(monkeypatch, prices)
    # Only "Banking" sector (TMB in the seed ledger) should stay eligible.
    cyc = scanner_service.run(
        PartBRunRequest(universe_ids=["holdings"], filters=ScannerFilters(sectors=["Banking"])),
        now_ist=now_ist,
    )
    out = cyc.steps.part_b_scanner.output
    for c in out.candidates:
        if c.sector != "Banking":
            assert not c.eligible and "sector_excluded" in c.flags
    assert out.eligible_count == 1


# --- Spec v2.3: the live formula is frozen -----------------------------------

@pytest.mark.parametrize("formula", [
    "6 Month Performance / 1 Month Volatility",          # a valid alternative
    "6 Month Momentum / 1 Month Volatility",             # not even a valid formula
    scoring_formula.FROZEN_FORMULA.replace("60%", "70%"),   # one weight nudged
])
def test_a_different_formula_is_refused_before_anything_runs(at_part_b, monkeypatch,
                                                             now_ist, formula):
    monkeypatch.setattr(scanner_service.stock_prices, "get_prices",
                        lambda *a, **k: pytest.fail("prices must not be fetched"))
    with pytest.raises(FormulaFrozen) as exc:
        scanner_service.run(PartBRunRequest(universe_ids=["holdings"], formula=formula),
                            now_ist=now_ist)

    err = exc.value
    assert err.http_status == 409 and err.code == "formula_frozen"
    assert err.to_dict()["frozen_formula"] == scoring_formula.FROZEN_FORMULA
    # Refused, not failed: the step is untouched and can still be run properly.
    step = cycle_service.get_current_cycle().steps.part_b_scanner
    assert step.status == StepStatus.PENDING and step.output is None


@pytest.mark.parametrize("formula", [
    "",                                                  # blank = the frozen default
    "   ",
    scoring_formula.FROZEN_FORMULA,
    scoring_formula.FROZEN_FORMULA.lower().replace(" + ", "+").replace("*", "x"),
])
def test_the_frozen_formula_is_always_what_gets_scored(at_part_b, monkeypatch, now_ist,
                                                       formula):
    _patch_prices(monkeypatch, {"TMB.NS": _ohlcv(np.linspace(100, 300, 400))})
    cyc = scanner_service.run(PartBRunRequest(universe_ids=["holdings"], formula=formula),
                              now_ist=now_ist)

    step = cyc.steps.part_b_scanner
    assert step.status == StepStatus.DONE
    # The canonical text is recorded, whatever spelling was sent.
    assert step.output.formula == scoring_formula.FROZEN_FORMULA
    assert step.input["formula"] == scoring_formula.FROZEN_FORMULA


def test_a_scoring_failure_lands_on_the_step(at_part_b, monkeypatch, now_ist):
    def boom(*a, **k):
        raise RuntimeError("price feed down")

    monkeypatch.setattr(scanner_service.stock_prices, "get_prices", boom)
    cyc = scanner_service.run(PartBRunRequest(universe_ids=["holdings"]), now_ist=now_ist)

    step = cyc.steps.part_b_scanner
    assert step.status == StepStatus.FAILED and step.output is None
    assert step.error == "RuntimeError: price feed down"


def test_list_sectors_is_the_sorted_union_without_blanks(at_part_b):
    assert scanner_service.list_sectors(["holdings"]) == sorted({sec for _, sec in HOLDINGS})


def test_multi_universe_union_dedup(at_part_b, monkeypatch, now_ist):
    # A fake NSE universe overlapping holdings on TMB; union should dedup it.
    fake = pd.DataFrame({"symbol": ["TMB", "NEWCO"], "sector": ["Banking", "Tech"], "company": ["", ""]})
    monkeypatch.setattr(scanner_service.constituents, "fetch_constituents", lambda key, force_refresh=False: fake)
    prices = {t: _ohlcv(np.linspace(100, 300, 400)) for t in
              ["SANSERA.NS", "TMB.NS", "KIRLOSENG.NS", "SONACOMS.NS", "ATHERENERG.NS", "NEWCO.NS"]}
    _patch_prices(monkeypatch, prices)

    cyc = scanner_service.run(
        PartBRunRequest(universe_ids=["holdings", "nifty50"]), now_ist=now_ist)
    out = cyc.steps.part_b_scanner.output
    symbols = [c.symbol for c in out.candidates]
    assert out.total == 6                       # 5 holdings + NEWCO (TMB deduped)
    assert symbols.count("TMB") == 1
    assert "NEWCO" in symbols
    assert "NIFTY 50" in out.universe_label and "Current Holdings" in out.universe_label


def test_advance_part_b_moves_to_part_c(at_part_b, monkeypatch, now_ist):
    _patch_prices(monkeypatch, {"TMB.NS": _ohlcv(np.linspace(100, 300, 400))})
    scanner_service.run(PartBRunRequest(universe_ids=["holdings"]), now_ist=now_ist)
    cyc = cycle_service.advance_part_b(now=now_ist)
    assert cyc.current_step == "part_c_exit_flags"
