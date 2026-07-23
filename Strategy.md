# Master Momentum Specification (v2.2)
**Pure momentum stock trading system — architecture pipeline**
Status: CONFIRMED FOR CODING | Governing policy: Rulebook v1.1 | Amended: July 2026

## Amendment Log (v2.1 -> v2.2)
1. Veto Rule 7 ("within 5% of 20-day EMA") DELETED — vetoed nearly all momentum candidates; made the 30% extension check unreachable.
2. Entry window restored: replacement loop iterates Ranks 1-15 only; unfilled slots stay in cash until next Friday.
3. Listing-age rule restored: listed < 12 months -> drop (Check 7).
4. GTT cancellation on RED: per-position at Monday exit-order placement, never bulk Friday evening (weekend-gap protection).

## System States
- HOLDINGS_LEDGER: [Ticker, Quantity, Purchase_Price, Entry_Date, Current_GTT_Stop]
- SYSTEM_REGIME: GREEN or RED
- CASH_BALANCE: investable liquid funds
- SCANNER_RANK_LIST: from Green Sigma scanner

## Phase 1: Mid-Week Passive Monitoring (Tue-Thu, 09:15-15:30)
- 1.1 Poll daily lows of holdings. If Daily_Low <= Current_GTT_Stop -> liquidation event (broker GTT fires; ledger mirrors).
- 1.2 Exit price = min(Current_GTT_Stop, day's open) — handles gap-downs.
- 1.3 Remove ticker; CASH_BALANCE += Qty x Exit_Price. INVARIANT: cash quarantined until next Friday ritual.

## Phase 2: Friday Evening Ritual (15:30-17:00)
### Part A — Regime Gate
- 2.1 Fetch weekly candles, NIFTY MIDSMALLCAP 400.
- 2.2 Weekly Supertrend: ATR period 1, multiplier 2.5.
- 2.3 RED (close below line): regime=RED; mark all holdings for Monday-open liquidation; skip scan/rank/veto; write journal; terminate. Stops are NOT cancelled Friday — each stop is cancelled only when its Monday exit order is placed.
- 2.3 GREEN (close above line): regime=GREEN; proceed to Part B.
- NOTE: RED->GREEN re-entry is implicit and intentional — empty ledger + Part D slot-filling rebuys ~10 names on the first GREEN Friday (Rulebook v1.1 S2). Do not add a special case.

### Part B — Scan & Rank (GREEN only)
- 2.4 Pull constituents of both indices.
- 2.5 Drop tickers with 20-day median daily traded value < Rs 5 crore.
- 2.6 Rank via scanner -> SCANNER_RANK_LIST.

### Part C — Portfolio Audit
- 2.7 Holding rank > 50, or dropped from both indices (rank = infinity) -> flag EXIT_ORDER.

### Part D — Replacements Loop
- 2.8 Open slots = target 8-12 names minus survivors.
- 2.9 Veto filter (eliminate-only). ITERATE RANKS 1-15 ONLY; unfilled slots stay cash. Checks:
  1. Promoter pledging > 20%?
  2. Interest coverage < 2 (non-financials)?
  3. Price below 50-DMA?
  4. Price > 30% above 20-DMA?
  5. RSI(14) > 82?
  6. Pending structural corporate/regulatory action?
  7. Listed < 12 months?
  Any true -> drop, next rank. Missing data -> "unverified", passes with flag. Filter never ranks/picks/adds.
- 2.10 Caps: max 3 per sector, max 4 Microcap. Breach -> skip (within ranks 1-15). Pass -> NEW_BUY_ORDER.

### Part E — Finalize
- 2.11 Journal row: date, regime, holding ranks, exits, strike-outs with cited reasons, unfilled slots, stops verified (Y/N).

## Phase 3: Monday Execution (09:15-09:45) — MANUAL until promotion
- 3.1 Liquidations first: per ticker, cancel its GTT, then place sell. Never bulk-cancel. Update cash on fills.
- 3.2 Deploy cash into NEW_BUY_ORDER at ~10% weight. Grandfathered legacy positions untouched.
- 3.3 Stops: stop = fill price x 0.80; place GTT same session.
- 3.4 Clear queues; back to Phase 1.

## Build Sequence
1. Phase 2 Parts A-C read-only first (fetch/compute/compare/print; no orders).
2. Run beside 2-3 manual Fridays; log divergences with root cause.
3. Add Part D plan generation; shadow per ratified ladder (12 weeks, >=10 identical plans, both fire drills, zero unexplained).
4. Broker-API execution LAST — only after January 2027 promotion. Parameters are never tuned by the bot; parameter authority is human-only, at scheduled reviews.

Governing authority remains the one-page Rulebook v1.1 and its journal. This spec implements it; it does not amend it.
