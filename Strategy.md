# Master Momentum Specification (v2.4)
**Pure momentum stock trading system — architecture pipeline**
Status: CONFIRMED FOR CODING | Governing policy: Rulebook v1.1 | Amended: November 2026

## Amendment Log (v2.3 -> v2.4) — corrections ordered by the owner, 15 November 2026
Twenty-one defects found by the test suite were fixed. None changes a threshold;
each makes the system do what the Rulebook already said. The ones that bear on
a trading decision:
1. DATA GAPS ARE NEVER A SIGNAL. A name whose latest price is more than 7 days older
   than the as-of date is not ranked. A holding that could not be priced, or that
   falls outside the scan's optional sector/price filter, is KEPT and marked "not
   audited" — it is no longer flagged "dropped from universe" and queued for sale.
2. THE SCAN COVERS THE TWO INDICES BY DEFAULT (it defaulted to the holdings alone,
   where no rank exit can ever fire). Index membership lists are re-fetched after
   30 days instead of being kept forever.
3. THE GATE MUST JUDGE THIS WEEK. An index source whose candles stop before the
   latest closed week is skipped (a forgotten manual file can no longer feed the
   gate), and a verdict for a different week than the cycle's is flagged.
4. THE SECTOR CAP COUNTS BY NSE INDUSTRY, not by label text, so a holding recorded
   as "Auto Components" counts against a candidate under "Automobile and Auto
   Components".
5. CHECKS THAT DID NOT RUN SAY SO. Unreachable NSE surveillance lists are reported
   as "not checked" (never "not on ASM/GSM"); an unavailable Microcap 250 list is
   reported as "cap not enforced"; a missing interest-expense figure is unverified,
   not "debt free". Interest coverage from pre-tax income adds the interest back.
6. THE RITUAL CANNOT BE STEPPED OUT OF ORDER. Advancing a step that is not the
   current one is refused, and re-advancing the journal replaces the week's row
   instead of adding a second.
7. THE BACKTEST SIZES LIKE THE LIVE PLAN. A buy that cannot be funded to 95% of its
   target is deferred, never opened as a sliver with leftover cash.

## Amendment Log (v2.2 -> v2.3) — ratified by the owner, 14 November 2026
Each of these came out of the July 2026 backtests and sweeps. They change cash handling,
the gate's index and what the research reports — never a signal threshold.
1. RED-week and idle cash is PARKED in a liquid or overnight fund, never left idle. Across
   the July runs idle RED cash cost roughly 2-4 points of CAGR a year. Parking is cash
   management only: it changes no entry, exit or regime decision.
2. The regime gate runs on NIFTY 500 (was NIFTY MIDSMALLCAP 400). Every backtest gated on
   NIFTY 500; MIDSMALLCAP 400 is only reachable as a young ETF proxy, so that gate was
   never tested over a full cycle. The stock UNIVERSE is unchanged.
3. The scoring formula is FROZEN at
   `((60% * 6M Perf) + (30% * 3M Perf) + (10% * 1M Perf)) / (3M Vol + 1M Vol)`.
   Walk-forward sweeps showed a formula chosen on 2006-2016 does not reliably win
   afterwards (on the 400+250 universe the training winner landed in the 25th percentile
   out of sample). The live ritual refuses any other formula; research may still try them.
4. Backtests report AFTER TAX (short-term 20%, long-term 12.5% above Rs 1.25 lakh a year,
   parked-cash income at 30%) and can be measured against a TOTAL-RETURN benchmark
   (NIFTY MIDSMALLCAP 400 TRI). A pre-tax result against a price index is reported as a
   stated limitation, not as the headline.
5. Gold is NOT adopted for RED weeks. It showed 23% in the backtest, but that leans on
   gold's 20-year run and adds a second bet. The parking instrument is a liquid or
   overnight fund only.
6. Promotion to broker execution is re-dated. The journal was reset on 14 November 2026,
   so the twelve shadow weeks restart with the 2026-11-13 cycle and complete on
   29 January 2027 at the earliest — one week later for every Friday missed.

NOTE: amendments 1, 2, 3 and 5 change what Rulebook v1.1 says. The Rulebook is the
governing document and lives outside this repository — mirror them there.

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
- 1.3 Remove ticker; CASH_BALANCE += Qty x Exit_Price. INVARIANT: cash quarantined until next Friday ritual — parked in the liquid or overnight fund meanwhile, never redeployed mid-week.

## Phase 2: Friday Evening Ritual (15:30-17:00)
### Part A — Regime Gate
- 2.1 Fetch weekly candles, NIFTY 500 (v2.3 — the index the backtests gated on). Another index may be run for comparison, but its decision is flagged UNTESTED and is not to be acted on.
- 2.2 Weekly Supertrend: ATR period 1, multiplier 2.5.
- 2.3 RED (close below line): regime=RED; mark all holdings for Monday-open liquidation; skip scan/rank/veto; write journal; terminate. Stops are NOT cancelled Friday — each stop is cancelled only when its Monday exit order is placed. The Monday proceeds are parked in a liquid or overnight fund (v2.3) until the first GREEN Friday.
- 2.3 GREEN (close above line): regime=GREEN; proceed to Part B.
- NOTE: RED->GREEN re-entry is implicit and intentional — empty ledger + Part D slot-filling rebuys ~10 names on the first GREEN Friday (Rulebook v1.1 S2). Do not add a special case.

### Part B — Scan & Rank (GREEN only)
- 2.4 Pull constituents of both indices.
- 2.5 Drop tickers with 20-day median daily traded value < Rs 5 crore. Also do not rank a ticker whose latest price is more than 7 days old (v2.4) — it cannot be traded at that price.
- 2.6 Rank via the FROZEN scoring formula (v2.3, amendment 3) -> SCANNER_RANK_LIST. The formula is not editable in the ritual; changing it is a Rulebook amendment.

### Part C — Portfolio Audit
- 2.7 Holding rank > 50, or dropped from both indices (rank = infinity) -> flag EXIT_ORDER. A holding that could not be ranked because its price is missing or stale, or because the scan's own sector/price filter excluded it, is KEPT and reported as not audited (v2.4): a data gap is not an exit signal.

### Part D — Replacements Loop
- 2.8 Open slots = target 8-12 names minus survivors.
- 2.9 Veto filter (eliminate-only). ITERATE RANKS 1-15 ONLY; unfilled slots stay cash (parked in the liquid or overnight fund, v2.3). Checks:
  1. Promoter pledging > 20%?
  2. Interest coverage < 2 (non-financials)?
  3. Price below 50-DMA?
  4. Price > 30% above 20-DMA?
  5. RSI(14) > 82?
  6. Pending structural corporate/regulatory action?
  7. Listed < 12 months?
  Any true -> drop, next rank. Missing data -> "unverified", passes with flag. Filter never ranks/picks/adds.
- 2.10 Caps: max 3 per sector (counted by NSE industry, v2.4), max 4 Microcap. Breach -> skip (within ranks 1-15). Pass -> NEW_BUY_ORDER. If the Microcap list cannot be loaded the step says the cap was not enforced.

### Part E — Finalize
- 2.11 Journal row: date, regime, holding ranks, exits, strike-outs with cited reasons, unfilled slots, stops verified (Y/N).

## Phase 3: Monday Execution (09:15-09:45) — MANUAL until promotion
- 3.0 Before the open (GREEN weeks): whatever the buys need beyond this week's exit proceeds must be back from the liquid or overnight fund and in the trading account. Redeem early enough for your fund's payout time.
- 3.1 Liquidations first: per ticker, cancel its GTT, then place sell. Never bulk-cancel. Update cash on fills.
- 3.2 Deploy cash into NEW_BUY_ORDER at ~10% weight. Grandfathered legacy positions untouched.
- 3.3 Stops: stop = fill price x 0.80; place GTT same session.
- 3.4 Clear queues; back to Phase 1.
- 3.5 Park what is not deployed, the same day, in the liquid or overnight fund: all proceeds on a RED week; on a GREEN week any remainder that is a slot short of the next 10% buy. Never gold, never an undersized position.

## Build Sequence
1. Phase 2 Parts A-C read-only first (fetch/compute/compare/print; no orders).
2. Run beside 2-3 manual Fridays; log divergences with root cause.
3. Add Part D plan generation; shadow per ratified ladder (12 weeks, >=10 identical plans, both fire drills, zero unexplained).
4. Broker-API execution LAST — only after promotion, which is not before 29 January 2027 (the twelfth consecutive shadow Friday counting from the 2026-11-13 cycle; each missed Friday pushes it back a week). Parameters are never tuned by the bot; parameter authority is human-only, at scheduled reviews.

Governing authority remains the one-page Rulebook v1.1 and its journal. This spec implements it; it does not amend it.
