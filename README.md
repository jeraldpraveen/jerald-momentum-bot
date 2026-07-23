# jerald-momentum-bot

Weekly momentum rotation system for Indian mid/small/microcaps, gated by a
binary regime filter (Rulebook v1.1, frozen until Jan 2027).

## Part A — Regime Gate Evaluation (implemented)

Friday-close ritual: fetch weekly NIFTY MIDSMALLCAP 400 candles, compute
Weekly Supertrend (ATR period 1, multiplier 2.5), and branch:

* **GREEN** (close above the line): journal, flag mandatory re-entry if last
  week was RED, proceed to Part B. Exit code 0.
* **RED** (close below the line): mark all ACTIVE holdings `PENDING_EXIT`
  (sell Monday open), cancel all resting GTT stops via the broker interface,
  journal, terminate. Exit code 2.

```
python run_part_a.py        # dry run — no real broker calls
python -m pytest tests/ -q  # unit + branch integration tests
```

### Data sources (provider chain, first success wins)

1. `data/index_weekly_manual.csv` — manual export (columns
   `date,open,high,low,close`); authoritative override when present.
2. Official NSE `indicesHistory` API — attempted, but currently bot-blocked
   (403/503 from Akamai) for non-browser clients.
3. **`MIDSMALL.NS`** (Mirae Asset Nifty MidSmallcap 400 ETF) via Yahoo
   Finance — tracks the exact index; the working default today. Note the
   journal records prices in ETF units, not index points, when this source
   is used; the Supertrend *direction* is what gates the regime.

The journal (`data/journal.md` / `data/journal.jsonl`) records which source
fed every decision.

### Layout

```
momentum_bot/
  config.py       frozen parameters + paths
  data_fetch.py   Step 2.1 — weekly candles (provider chain)
  supertrend.py   Step 2.2 — Supertrend (1, 2.5)
  regime_gate.py  Step 2.3 — gate branch logic + state (RED->GREEN detection)
  ledger.py       HOLDINGS_LEDGER csv
  broker.py       Broker interface (DryRunBroker default; Kite placeholder)
  journal.py      journal.md + journal.jsonl writers
data/             ledger, state.json, journals, manual CSV override
run_part_a.py     entry point
```

## Part B — Scan / Veto / Fill (not yet implemented)
