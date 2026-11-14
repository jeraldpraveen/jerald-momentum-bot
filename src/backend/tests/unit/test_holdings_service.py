"""Step-0 holdings CRUD tests."""

import pandas as pd
import pytest

from app.schemas.holdings import Holding
from app.services import holdings_service


def test_list_reads_seeded_ledger(isolated):
    rows = holdings_service.list_holdings()
    assert {h.symbol for h in rows} == {"AAA", "BBB"}
    assert all(h.status == "ACTIVE" for h in rows)


def test_blank_numeric_cells_become_none(isolated):
    pd.DataFrame({
        "symbol": ["ZZZ"], "qty": [""], "entry_price": [""], "entry_date": [""],
        "stop_price": [""], "sleeve": [""], "sector": [""], "status": ["ACTIVE"],
        "exit_reason": [""], "marked_on": [""],
    }).to_csv(isolated / "holdings_ledger.csv", index=False)

    h = holdings_service.list_holdings()[0]
    assert h.qty is None and h.entry_price is None and h.stop_price is None
    assert h.entry_date == ""


def test_save_replaces_ledger_and_round_trips(isolated):
    saved = holdings_service.save_holdings([
        Holding(symbol="TCS", qty=10, entry_price=3500.5, entry_date="2026-06-01",
                stop_price=2800.4, sleeve="MIDSMALLCAP400", sector="IT", status="ACTIVE"),
        Holding(symbol="INFY", qty=5, entry_price=1500, status="PENDING_EXIT",
                exit_reason="rank_exit", marked_on="2026-07-17"),
    ])

    assert [h.symbol for h in saved] == ["TCS", "INFY"]
    tcs = saved[0]
    assert tcs.qty == 10 and tcs.entry_price == 3500.5 and tcs.stop_price == 2800.4
    assert tcs.entry_date == "2026-06-01" and tcs.sector == "IT"
    assert saved[1].status == "PENDING_EXIT" and saved[1].exit_reason == "rank_exit"

    # Persisted to disk, and the old rows are gone (replace-all).
    on_disk = pd.read_csv(isolated / "holdings_ledger.csv")
    assert set(on_disk["symbol"]) == {"TCS", "INFY"}

    # Re-reading gives the same typed values.
    assert holdings_service.list_holdings() == saved


def test_setting_status_active_clears_exit_metadata(isolated):
    # A name previously marked for exit...
    holdings_service.save_holdings([
        Holding(symbol="TMB", entry_price=633, status="PENDING_EXIT",
                exit_reason="rank_exit", marked_on="2026-07-21"),
    ])
    marked = holdings_service.list_holdings()[0]
    assert marked.exit_reason == "rank_exit" and marked.marked_on == "2026-07-21"

    # ...flipped back to ACTIVE must drop the stale reason/date.
    holdings_service.save_holdings([
        Holding(symbol="TMB", entry_price=633, status="ACTIVE",
                exit_reason="rank_exit", marked_on="2026-07-21"),
    ])
    back = holdings_service.list_holdings()[0]
    assert back.status == "ACTIVE"
    assert back.exit_reason == ""
    assert back.marked_on == ""

    on_disk = pd.read_csv(isolated / "holdings_ledger.csv").fillna("")
    assert on_disk.loc[0, "exit_reason"] == ""
    assert str(on_disk.loc[0, "marked_on"]) == ""


def test_non_active_keeps_exit_metadata(isolated):
    holdings_service.save_holdings([
        Holding(symbol="XYZ", status="EXITED", exit_reason="stop_hit", marked_on="2026-07-15"),
    ])
    h = holdings_service.list_holdings()[0]
    assert h.exit_reason == "stop_hit" and h.marked_on == "2026-07-15"


def test_save_empty_list_clears_ledger(isolated):
    assert holdings_service.save_holdings([]) == []
    assert holdings_service.list_holdings() == []


def test_lookup_unknown_symbol_is_graceful(isolated, monkeypatch):
    monkeypatch.setattr(holdings_service.constituents, "fetch_constituents",
                        lambda *a, **k: pd.DataFrame({"symbol": [], "sector": [], "company": []}))
    monkeypatch.setattr(holdings_service.stock_prices, "get_prices", lambda *a, **k: {})
    res = holdings_service.lookup_symbol("NOPE")
    assert res.found is False and res.sector == "" and res.last_close is None


def test_lookup_fills_sector_sleeve_and_price(isolated, monkeypatch):
    monkeypatch.setattr(
        holdings_service.constituents, "fetch_constituents",
        lambda *a, **k: pd.DataFrame({"symbol": ["SANSERA"], "sector": ["Auto Components"],
                                      "company": ["Sansera"]}))
    monkeypatch.setattr(
        holdings_service.stock_prices, "get_prices",
        lambda *a, **k: {"SANSERA.NS": pd.DataFrame({"close": [100.0, 3335.2]})})

    res = holdings_service.lookup_symbol("sansera")
    assert res.symbol == "SANSERA" and res.found
    assert res.sector == "Auto Components"
    assert res.sleeve == "MIDSMALLCAP400"      # first universe searched
    assert res.last_close == 3335.2


# --- portfolio valuation (capital deployed) ---------------------------------

def _prices(monkeypatch, closes: dict[str, float], dates: dict[str, str] | None = None):
    """Patch the price feed with a flat series per symbol, ending on a given date."""
    def fake(tickers, as_of=None, today=None, force=False):
        out = {}
        for t in tickers:
            sym = t.removesuffix(".NS")
            px = closes.get(sym)
            if px is not None:
                end = (dates or {}).get(sym, "2026-07-22")
                idx = pd.date_range(end=end, periods=5, freq="D")
                out[t] = pd.DataFrame({"close": [px] * 5}, index=idx)
        return out
    monkeypatch.setattr(holdings_service.stock_prices, "get_prices", fake)


def test_summary_totals_deployed_value_and_pnl(isolated, monkeypatch):
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50, stop_price=40),
        Holding(symbol="BBB", qty=10, entry_price=200, stop_price=160),
    ])
    _prices(monkeypatch, {"AAA": 60.0, "BBB": 180.0})

    s = holdings_service.summarize()
    assert s.capital_deployed == pytest.approx(7000)      # 5000 + 2000
    assert s.market_value == pytest.approx(7800)          # 6000 + 1800
    assert s.unrealised_pnl == pytest.approx(800)
    assert s.unrealised_pnl_pct == pytest.approx(11.43, abs=0.01)
    assert s.held_count == 2 and s.active_count == 2


def test_summary_per_position_pnl_and_weights(isolated, monkeypatch):
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50, stop_price=40),
        Holding(symbol="BBB", qty=10, entry_price=200, stop_price=160),
    ])
    _prices(monkeypatch, {"AAA": 60.0, "BBB": 180.0})

    by_sym = {p.symbol: p for p in holdings_service.summarize().positions}
    assert by_sym["AAA"].pnl == pytest.approx(1000)
    assert by_sym["AAA"].pnl_pct == pytest.approx(20.0)
    assert by_sym["BBB"].pnl == pytest.approx(-200)
    assert sum(p.weight_pct for p in by_sym.values()) == pytest.approx(100, abs=0.05)


def test_open_risk_is_distance_to_stop(isolated, monkeypatch):
    holdings_service.save_holdings([Holding(symbol="AAA", qty=100, entry_price=50, stop_price=40)])
    _prices(monkeypatch, {"AAA": 60.0})
    # 100 sh x (60 - 40) = 2000 at risk if the stop fires from here.
    assert holdings_service.summarize().open_risk == pytest.approx(2000)


def test_pending_exit_still_counts_as_deployed(isolated, monkeypatch):
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50, status="PENDING_EXIT",
                exit_reason="rank_exit"),
    ])
    _prices(monkeypatch, {"AAA": 60.0})

    s = holdings_service.summarize()
    assert s.capital_deployed == pytest.approx(5000)   # you still hold it on Friday
    assert s.pending_exit_count == 1 and s.active_count == 0


def test_exited_rows_are_excluded_and_flagged(isolated, monkeypatch):
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50),
        Holding(symbol="OLD", qty=10, entry_price=100, status="EXITED"),
    ])
    _prices(monkeypatch, {"AAA": 60.0, "OLD": 90.0})

    s = holdings_service.summarize()
    assert s.capital_deployed == pytest.approx(5000)      # OLD's 1000 not counted
    assert s.exited_count == 1
    assert [p.symbol for p in s.positions] == ["AAA"]
    assert any("realised P&L" in w for w in s.warnings)


def test_incomplete_rows_are_named_not_silently_zeroed(isolated, monkeypatch):
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50),
        Holding(symbol="NOQTY", entry_price=50),
    ])
    _prices(monkeypatch, {"AAA": 60.0, "NOQTY": 55.0})

    s = holdings_service.summarize()
    assert s.capital_deployed == pytest.approx(5000)
    assert s.incomplete == ["NOQTY"]
    assert any("no qty or entry price" in w for w in s.warnings)


def test_pnl_ignores_cost_of_unpriced_names(isolated, monkeypatch):
    """A name we cannot price must not drag P&L down as if it went to zero."""
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50),
        Holding(symbol="DARK", qty=10, entry_price=100),
    ])
    _prices(monkeypatch, {"AAA": 60.0})          # DARK has no price

    s = holdings_service.summarize()
    assert s.capital_deployed == pytest.approx(6000)   # cost still counts
    assert s.market_value == pytest.approx(6000)       # only AAA valued
    assert s.unrealised_pnl == pytest.approx(1000)     # vs AAA's 5000 cost, not 6000
    assert s.unpriced == ["DARK"]


def test_empty_ledger_summarizes_cleanly(isolated, monkeypatch):
    holdings_service.save_holdings([])
    _prices(monkeypatch, {})

    s = holdings_service.summarize()
    assert s.held_count == 0
    assert s.capital_deployed is None and s.unrealised_pnl is None
    assert s.positions == []


def test_a_symbol_priced_a_session_behind_is_named(isolated, monkeypatch):
    """One lagging ticker must not hide behind a portfolio-wide 'as of' date."""
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50),
        Holding(symbol="LAGGY", qty=10, entry_price=200),
    ])
    _prices(monkeypatch, {"AAA": 60.0, "LAGGY": 210.0},
            dates={"AAA": "2026-07-22", "LAGGY": "2026-07-21"})

    s = holdings_service.summarize()
    assert s.price_date == "2026-07-22"          # headline = the newest close used
    assert s.stale_prices == ["LAGGY"]
    assert any("LAGGY" in w and "older close" in w for w in s.warnings)
    by = {p.symbol: p for p in s.positions}
    assert by["LAGGY"].price_date == "2026-07-21"


def test_uniform_price_dates_raise_no_staleness_warning(isolated, monkeypatch):
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50),
        Holding(symbol="BBB", qty=10, entry_price=200),
    ])
    _prices(monkeypatch, {"AAA": 60.0, "BBB": 210.0})

    s = holdings_service.summarize()
    assert s.stale_prices == []
    assert not any("older close" in w for w in s.warnings)


def test_summary_refresh_forces_a_live_pull(isolated, monkeypatch):
    holdings_service.save_holdings([Holding(symbol="AAA", qty=10, entry_price=100)])
    seen: list[bool] = []

    def fake(tickers, as_of=None, today=None, force=False):
        seen.append(force)
        idx = pd.date_range(end="2026-07-22", periods=3, freq="D")
        return {t: pd.DataFrame({"close": [120.0] * 3}, index=idx) for t in tickers}

    monkeypatch.setattr(holdings_service.stock_prices, "get_prices", fake)

    holdings_service.summarize()
    holdings_service.summarize(refresh=True)
    assert seen == [False, True]


# --- ledger durability ------------------------------------------------------

def test_a_crash_mid_write_leaves_the_previous_ledger_intact(isolated, monkeypatch):
    """The ledger is the one irreplaceable file here. A half-written CSV would
    lose real positions, so the write must be atomic (temp + os.replace)."""
    from app import config
    from app.repositories import ledger_repository as L

    holdings_service.save_holdings([Holding(symbol="AAA", qty=10, entry_price=100)])
    before = config.HOLDINGS_LEDGER.read_text(encoding="utf-8")

    # Explode after the temp file is written but before it is swapped in.
    def boom(src, dst):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(L.os, "replace", boom)
    with pytest.raises(OSError):
        holdings_service.save_holdings([Holding(symbol="BBB", qty=99, entry_price=1)])

    assert config.HOLDINGS_LEDGER.read_text(encoding="utf-8") == before
    assert [h.symbol for h in holdings_service.list_holdings()] == ["AAA"]


def test_every_save_snapshots_the_previous_ledger(isolated):
    from app.repositories import ledger_repository as L

    holdings_service.save_holdings([Holding(symbol="AAA", qty=10, entry_price=100)])
    holdings_service.save_holdings([Holding(symbol="BBB", qty=20, entry_price=200)])

    backups = L.list_backups()
    assert backups, "a snapshot should exist before the ledger is overwritten"
    assert "AAA" in backups[0].read_text(encoding="utf-8")


def test_a_bad_edit_can_be_rolled_back(isolated):
    """Atomicity does not protect against a fully-written WRONG file — a bad
    edit, or Excel reformatting it. Backups are what make that recoverable."""
    from app.repositories import ledger_repository as L

    holdings_service.save_holdings([
        Holding(symbol="SANSERA", qty=25, entry_price=3239.52),
        Holding(symbol="TMB", qty=100, entry_price=791.82),
    ])
    holdings_service.save_holdings([])              # oops — wiped the ledger
    assert holdings_service.list_holdings() == []

    assert L.restore_backup(L.list_backups()[0].name) is True
    assert {h.symbol for h in holdings_service.list_holdings()} == {"SANSERA", "TMB"}


def test_backups_are_capped(isolated, monkeypatch):
    from app.repositories import ledger_repository as L
    monkeypatch.setattr(L, "BACKUP_KEEP", 3)

    for i in range(6):
        holdings_service.save_holdings([Holding(symbol=f"S{i}", qty=1, entry_price=1)])
    assert len(L.list_backups()) <= 3


def test_a_malformed_frame_is_refused(isolated):
    """Better to fail loudly than to write a ledger later code cannot read."""
    from app.repositories import ledger_repository as L
    with pytest.raises(ValueError, match="unexpected columns"):
        L.save_ledger(pd.DataFrame({"nonsense": [1]}))


def test_restoring_an_unknown_backup_reports_false(isolated):
    from app.repositories import ledger_repository as L
    assert L.restore_backup("holdings_ledger.19990101-000000.csv") is False


# --- exit price & realised P&L ----------------------------------------------

def test_exit_price_round_trips_through_the_ledger(isolated):
    holdings_service.save_holdings([
        Holding(symbol="TMB", qty=100, entry_price=791.82, status="EXITED",
                exit_reason="rank_exit", exit_price=820.0),
    ])
    h = holdings_service.list_holdings()[0]
    assert h.status == "EXITED" and h.exit_price == 820.0


def test_active_status_clears_any_exit_price(isolated):
    """An ACTIVE position cannot carry an exit price — flipping back clears it."""
    saved = holdings_service.save_holdings([
        Holding(symbol="AAA", qty=10, entry_price=100, status="ACTIVE", exit_price=150.0),
    ])
    assert saved[0].exit_price is None


def test_realised_pnl_from_exited_positions(isolated, monkeypatch):
    _prices(monkeypatch, {"AAA": 60.0})       # one ACTIVE name still held
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=50),                    # unrealised
        Holding(symbol="TMB", qty=100, entry_price=791.82, status="EXITED",
                exit_reason="rank_exit", exit_price=820.0),                # +2818 realised
        Holding(symbol="XYZ", qty=50, entry_price=200, status="EXITED",
                exit_price=180.0),                                         # -1000 realised
    ])
    s = holdings_service.summarize()
    assert s.realised_count == 2
    assert s.realised_pnl == pytest.approx(100 * (820 - 791.82) + 50 * (180 - 200))
    # unrealised still only covers the ACTIVE name
    assert s.unrealised_pnl == pytest.approx(100 * (60 - 50))


def test_exited_without_exit_price_is_warned_not_counted(isolated, monkeypatch):
    _prices(monkeypatch, {})
    holdings_service.save_holdings([
        Holding(symbol="TMB", qty=100, entry_price=791.82, status="EXITED"),   # no exit price
    ])
    s = holdings_service.summarize()
    assert s.realised_count == 0 and s.realised_pnl is None
    assert any("EXITED without an exit price" in w for w in s.warnings)


# --- charges (entry + exit) -------------------------------------------------

def test_charges_round_trip_and_net_realised_pnl(isolated, monkeypatch):
    _prices(monkeypatch, {})
    holdings_service.save_holdings([
        Holding(symbol="TMB", qty=100, entry_price=791.82, status="EXITED",
                exit_price=820.0, entry_charges=30.0, exit_charges=35.0),
    ])
    h = holdings_service.list_holdings()[0]
    assert h.entry_charges == 30.0 and h.exit_charges == 35.0

    s = holdings_service.summarize()
    gross = 100 * (820.0 - 791.82)
    assert s.realised_pnl == pytest.approx(gross - 30.0 - 35.0)   # net of both legs


def test_entry_charges_raise_the_cost_basis(isolated, monkeypatch):
    _prices(monkeypatch, {"AAA": 100.0})
    holdings_service.save_holdings([
        Holding(symbol="AAA", qty=100, entry_price=100.0, entry_charges=200.0),
    ])
    s = holdings_service.summarize()
    assert s.capital_deployed == pytest.approx(100 * 100 + 200)   # cost includes buy charges
    # market value 100x100 = 10000, cost 10200 -> small loss from charges
    assert s.unrealised_pnl == pytest.approx(10000 - 10200)


def test_active_status_clears_exit_charges(isolated):
    saved = holdings_service.save_holdings([
        Holding(symbol="AAA", qty=10, entry_price=100, status="ACTIVE",
                exit_price=150.0, exit_charges=20.0, entry_charges=15.0),
    ])
    assert saved[0].exit_charges is None
    assert saved[0].entry_charges == 15.0        # buy charges are kept
