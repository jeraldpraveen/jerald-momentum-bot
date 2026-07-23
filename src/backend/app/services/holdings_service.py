"""Step-0 — Holdings service: CRUD on the persistent HOLDINGS_LEDGER.

Not a gated cycle step — holdings change when trades fill, independently of the
weekly ritual, so this is a plain read/replace-all API plus a symbol lookup that
auto-fills sector/sleeve/last price when the user types a ticker.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config
from ..integrations import constituents, stock_prices
from ..repositories import ledger_repository as ledger
from ..schemas.holdings import Holding, PortfolioSummary, PositionValue, SymbolLookup

log = logging.getLogger(__name__)

# Which index a symbol belongs to -> the sleeve label used by the risk caps.
_SLEEVE_BY_UNIVERSE = {"midsmall400": "MIDSMALLCAP400", "microcap250": "MICROCAP250"}
# Search the rulebook universes first, then the broader ones.
_LOOKUP_ORDER = ["midsmall400", "microcap250", "nifty500", "nifty200", "nifty100", "nifty50"]


def list_holdings() -> list[Holding]:
    return [Holding(**h) for h in ledger.to_holdings(ledger.load_ledger())]


def _normalize(h: Holding) -> dict:
    """Enforce ledger invariants before writing.

    An ACTIVE position carries no exit metadata — flipping a name back to ACTIVE
    (e.g. undoing a rank exit) must clear the stale reason/date.
    """
    d = h.model_dump()
    d["symbol"] = str(d.get("symbol", "")).strip().upper()
    if d.get("status") == "ACTIVE":
        d["exit_reason"] = ""
        d["marked_on"] = ""
    return d


def save_holdings(holdings: list[Holding]) -> list[Holding]:
    """Replace the whole ledger with the supplied rows."""
    df = ledger.from_holdings([_normalize(h) for h in holdings])
    ledger.save_ledger(df)
    return list_holdings()


HELD_STATUSES = ("ACTIVE", "PENDING_EXIT")


def _last_closes(symbols: list[str], force: bool = False) -> dict[str, tuple[float, str]]:
    """Returns {symbol: (last close, the date THAT close is from)}.

    The date is per symbol on purpose: one ticker lagging a session behind the
    rest used to disappear into a single portfolio-wide "as of" date.
    """
    if not symbols:
        return {}
    try:
        prices = stock_prices.get_prices([f"{s}.NS" for s in symbols], force=force)
    except Exception as exc:  # noqa: BLE001 — valuation is best-effort, never fatal
        log.warning("Portfolio valuation prices unavailable (%s)", type(exc).__name__)
        return {}
    out: dict[str, tuple[float, str]] = {}
    for s in symbols:
        df = prices.get(f"{s}.NS")
        if df is not None and not df.empty:
            out[s] = (round(float(df["close"].iloc[-1]), 2), df.index[-1].date().isoformat())
    return out


def summarize(refresh: bool = False) -> PortfolioSummary:
    """Capital deployed vs current market value, per position and in total.

    `refresh` forces a live price pull instead of serving the cache — what the
    "Refresh LTP" button calls.

    Deliberately partial-tolerant: a row missing qty or entry price still shows,
    it just contributes nothing to the totals and is named in `incomplete`, so a
    half-filled ledger reports honestly instead of understating silently.
    """
    holdings = list_holdings()
    held = [h for h in holdings if h.status in HELD_STATUSES]
    closes = _last_closes([h.symbol for h in held], force=refresh)

    positions: list[PositionValue] = []
    incomplete: list[str] = []
    unpriced: list[str] = []
    deployed = market_value = open_risk = 0.0
    priced_cost = 0.0        # cost of the names we could also value, for a like-for-like P&L

    for h in held:
        cost = h.qty * h.entry_price if (h.qty and h.entry_price) else None
        close, close_date = closes.get(h.symbol, (None, ""))
        mv = h.qty * close if (h.qty and close) else None
        if cost is None:
            incomplete.append(h.symbol)
        else:
            deployed += cost
        if close is None:
            unpriced.append(h.symbol)
        if mv is not None:
            market_value += mv
            if cost is not None:
                priced_cost += cost
        risk = (h.qty * (close - h.stop_price)) if (h.qty and close and h.stop_price) else None
        if risk:
            open_risk += max(risk, 0.0)

        positions.append(PositionValue(
            symbol=h.symbol, status=h.status, qty=h.qty, entry_price=h.entry_price,
            cost=round(cost, 2) if cost else None,
            last_close=close, price_date=close_date,
            market_value=round(mv, 2) if mv else None,
            pnl=round(mv - cost, 2) if (mv is not None and cost is not None) else None,
            pnl_pct=round((mv / cost - 1) * 100, 2) if (mv is not None and cost) else None,
            stop_price=h.stop_price,
            risk=round(risk, 2) if risk else None,
        ))

    # Headline date = the newest close used; anything older is called out by name.
    dates = [p.price_date for p in positions if p.price_date]
    price_date = max(dates) if dates else ""
    stale_prices = sorted({p.symbol for p in positions if p.price_date and p.price_date < price_date})

    for p in positions:                    # weights only make sense once totals are known
        if p.market_value and market_value:
            p.weight_pct = round(p.market_value / market_value * 100, 2)

    pnl = round(market_value - priced_cost, 2) if market_value else None

    warnings: list[str] = []
    if incomplete:
        warnings.append(
            f"{len(incomplete)} position(s) have no qty or entry price "
            f"({', '.join(incomplete[:5])}{'…' if len(incomplete) > 5 else ''}) — "
            "they are excluded from capital deployed.")
    if unpriced:
        warnings.append(
            f"No price for {', '.join(unpriced[:5])}{'…' if len(unpriced) > 5 else ''} — "
            "excluded from market value.")
    today = dt.date.today()
    if stale_prices:
        warnings.append(
            f"{', '.join(stale_prices)} priced at an older close than the rest of the "
            f"portfolio ({price_date}) — that position's value and P&L are behind.")
    if price_date and dt.date.fromisoformat(price_date) < today:
        warnings.append(
            f"Valued at the {price_date} close — your broker shows a live price, so "
            "these figures will differ intraday.")
    if any(h.status == "EXITED" for h in holdings):
        warnings.append(
            "EXITED positions are not valued — the ledger stores no exit price, "
            "so realised P&L is not available.")

    return PortfolioSummary(
        as_of=today.isoformat(), price_date=price_date,
        held_count=len(held),
        active_count=sum(1 for h in held if h.status == "ACTIVE"),
        pending_exit_count=sum(1 for h in held if h.status == "PENDING_EXIT"),
        exited_count=sum(1 for h in holdings if h.status == "EXITED"),
        capital_deployed=round(deployed, 2) or None,
        market_value=round(market_value, 2) or None,
        unrealised_pnl=pnl,
        unrealised_pnl_pct=round((market_value / priced_cost - 1) * 100, 2) if priced_cost else None,
        open_risk=round(open_risk, 2) or None,
        positions=positions, incomplete=incomplete, unpriced=unpriced,
        stale_prices=stale_prices, warnings=warnings,
    )


def lookup_symbol(symbol: str) -> SymbolLookup:
    """Best-effort sector / sleeve / last close for a ticker (auto-fill helper)."""
    sym = symbol.strip().upper()
    if not sym:
        return SymbolLookup(symbol=sym, found=False)

    sector, sleeve, found = "", "", False
    for uid in _LOOKUP_ORDER:
        try:
            opt = config.get_universe_option(uid)
            if opt.source != "nse_csv":
                continue
            df = constituents.fetch_constituents(opt.nse_key)
            hit = df[df["symbol"].astype(str).str.upper() == sym]
            if not hit.empty:
                sector = str(hit.iloc[0]["sector"])
                sleeve = _SLEEVE_BY_UNIVERSE.get(uid, opt.label)
                found = True
                break
        except Exception as exc:  # noqa: BLE001 — lookup is best-effort
            log.info("Constituent lookup failed for %s in %s (%s)", sym, uid, type(exc).__name__)

    last_close = None
    try:
        prices = stock_prices.get_prices([f"{sym}.NS"])
        df = prices.get(f"{sym}.NS")
        if df is not None and not df.empty:
            last_close = round(float(df["close"].iloc[-1]), 2)
            found = True
    except Exception as exc:  # noqa: BLE001
        log.info("Price lookup failed for %s (%s)", sym, type(exc).__name__)

    return SymbolLookup(symbol=sym, found=found, sector=sector, sleeve=sleeve, last_close=last_close)
