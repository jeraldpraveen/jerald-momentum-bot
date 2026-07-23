# Momentum Workbench

A weekly momentum-rotation **decision and journaling workbench** for Indian
mid/small/microcaps, implementing the frozen Master Momentum Specification
v2.2 (see [Strategy.md](Strategy.md)).

**It is not an execution engine.** It does not poll the market, place orders,
or cancel GTTs. It computes decisions, produces a by-hand Monday plan, and
keeps the ledger and journal. You trade manually; broker integration is the
January 2027 promotion in the spec.

---

## What it does

Each Friday after the close you walk eight gated steps. Nothing auto-advances —
running a step computes a preview, and *advancing* is a separate, explicit act
that commits changes to the ledger and journal.

| # | Step | What it decides |
|---|------|-----------------|
| 1 | **Regime Gate** | Weekly Supertrend (1, 2.5) on the chosen index → GREEN or RED. RED skips scan/rank/veto, writes the journal, and terminates. |
| 2 | **Jerald Score** | Ranks the universe by a risk-adjusted momentum score. Replaces the old Sigma Scanner Excel upload. |
| 3 | **Portfolio Audit** | Flags holdings ranked worse than 50, or dropped from the universe, for Monday exit. |
| 4 | **Fundamental** | Ranks 1–15 only: promoter pledging > 20%, interest cover < 2 (non-financials), pending structural action, listed < 12 months. |
| 5 | **Technical** | Price below the 50-DMA, more than 30% above the 20-DMA, or RSI(14) > 82. |
| 6 | **Risk Caps** | Max 3 per sector, max 4 microcap; fills 8–12 slots. Unfilled slots stay in cash. |
| 7 | **Journal** | Writes the week's permanent record — ranks, exits, buys, strike-outs with cited reasons. |
| 8 | **Monday Plan** | Liquidations first (per ticker: cancel that GTT, then sell), then buys at ~10% with stops at fill × 0.80. |

The veto ladder (4–6) is **eliminate-only** and sequential. A check that cannot
be verified from data is marked *unverified* and **passes with a flag** — a
missing number is never a strike.

## The UI

Four tabs:

- **Portfolio** — the holdings ledger, editable; capital deployed, market value,
  unrealised P&L, open risk, per-position LTP and P&L.
- **Friday Ritual** — the eight steps as an accordion, with per-step status.
- **History** — past cycles, read-only, with a clickable stage ribbon that
  replays what each step decided.
- **Backtest** — the research module (below). Entirely separate flow and data.

## Backtest

Replays the strategy over history using **the same rules the live ritual runs**.
Both callers import `app/domain/strategy/`, a pure core with no I/O, so research
provably tests the code you trade. A test (`test_strategy_core_has_no_io`) walks
the AST and fails if that core ever imports a service, repository or router.

Runs execute on a background thread — start one, poll for progress, leave the
tab. Its data lives under `app/data/backtest/` and can never touch the live
ledger, cycles or journal (also enforced by a test).

**Stated limitations**, reported with every run:

- **Survivorship bias** — a universe is today's index membership applied to all
  of history. Names that were delisted or demoted are absent, which flatters
  returns.
- **The fundamental veto is not simulated** — historical pledging and interest
  coverage are not obtainable. Per the data-safeguard, unverifiable checks pass.
- **Stop fills** assume `min(stop, next open)`; a real gap can be worse.
- Prices are yfinance adjusted closes, which differ slightly from NSE prints.

The **regime index — not the universe — limits how far back a run can go.**
`^CRSLDX` (NIFTY 500) reaches 2005; several others are ETF proxies that only
start at the fund's launch. The form warns before a run if your start date will
be clipped.

---

## Running it

Windows PowerShell (`&&` is not supported; run these separately):

```powershell
# backend — http://127.0.0.1:8000, docs at /docs
cd src/backend
python -m uvicorn app.main:app --reload

# frontend — http://localhost:5173, proxies /api to :8000
cd src/frontend
npm install
npm run dev
```

Tests and the type-generation step:

```powershell
cd src/backend
python -m pytest -q                 # 259 tests

cd src/frontend
npm run gen:api                     # regenerate TS types from the backend OpenAPI
npm run build
```

Python 3.14, deps in [requirements.txt](requirements.txt).

## Layout

```
src/
  backend/                  FastAPI — the only backend
    app/
      routers/              thin: parse request -> call service -> return
      services/             orchestration: load cycle, guard the gate, save
      repositories/         the only layer that touches disk
      domain/               pure math — no I/O
        strategy/           THE RULES, shared by the live app and the backtest
        supertrend.py, indicators.py, scoring_formula.py, momentum_score.py
      integrations/         read-only market/NSE data (yfinance, constituents,
                            ASM/GSM surveillance, fundamentals)
      schemas/              Pydantic — the source of truth for the OpenAPI
      backtest/             research engine: price store, universes, portfolio,
                            costs, metrics, weekly loop, run persistence
      data/                 runtime state (see below)
    tests/                  unit + integration
  frontend/                 React 18 + Vite + TypeScript
    src/features/           one folder per tab/step panel
    src/components/         AccordionStep, Tabs, MultiSelect, LineChart
  shared/                   openapi.json + generated TS types
```

### Data

One JSON file per weekly cycle — git-diffable, no database.

| Path (under `src/backend/app/data/`) | What | Versioned |
|------|------|-----------|
| `holdings_ledger.csv` | your live positions | yes |
| `journal.jsonl` / `.md` | the permanent weekly record | yes |
| `cycles/` + `_current_cycle.json` | working cycle state | no |
| `prices/`, `backtest/prices/` | downloaded OHLCV caches (~300 MB) | no |
| `backtest/runs/` | run config + headline metrics (the research log) | yes |
| `backtest/universes/` | universe snapshots (needed to reproduce results) | yes |
| `backtest/results/` | full curves, weeks, trades (~500 KB each) | no |

> **Before pushing this repo anywhere public**, uncomment the ledger and journal
> lines in [.gitignore](.gitignore) — they contain real positions, quantities and
> entry prices, and removing them after a push does not remove them from history.

### Design decisions worth knowing

- **One active cycle at a time.** Starting a second returns 409.
- **Preview then commit.** `run` computes; `advance` writes the ledger/journal.
  Re-opening a step invalidates everything downstream.
- **Close vs Delete a cycle.** *Close* keeps it as history and keeps its journal
  row; *Delete* removes both — a discarded test run leaves no trace. Neither
  reverts ledger changes already committed, because the app cannot know whether
  you have already acted on them.
- **Momentum lookbacks are fixed trading-candle counts**, `months × 21`
  (6M = 126), never calendar dates.
- **Price staleness is judged by the cache's last bar date**, never file
  mtime — a stale price is a wrong decision, not just a wrong display.
