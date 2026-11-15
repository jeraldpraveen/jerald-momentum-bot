# Momentum Workbench

A weekly momentum-rotation **decision and journaling workbench** for Indian
mid/small/microcaps, implementing the frozen Master Momentum Specification
v2.4 (see [Strategy.md](Strategy.md)).

**It is not an execution engine.** It does not poll the market, place orders,
or cancel GTTs. It computes decisions, produces a by-hand Monday plan, and
keeps the ledger and journal. You trade manually; broker integration waits for
promotion, which the spec puts no earlier than 29 January 2027 (twelve
consecutive shadow Fridays from the 2026-11-13 cycle).

---

## What it does

Each Friday after the close you walk eight gated steps. Nothing auto-advances —
running a step computes a preview, and *advancing* is a separate, explicit act
that commits changes to the ledger and journal.

| # | Step | What it decides |
|---|------|-----------------|
| 1 | **Regime Gate** | Weekly Supertrend (1, 2.5) on NIFTY 500 → GREEN or RED. RED skips scan/rank/veto, writes the journal, and terminates. Another index can be run, but its decision is flagged *untested*. |
| 2 | **Jerald Score** | Ranks the two Rulebook indices (the default) by a risk-adjusted momentum score. The formula is **frozen** (v2.3): it is shown, not edited, and the API refuses any other. A name whose latest price is over 7 days old is not ranked. |
| 3 | **Portfolio Audit** | Flags holdings ranked worse than 50, or dropped from the universe, for Monday exit. A holding that could not be priced, or that the scan's own filter excluded, is kept and reported as *not audited*. |
| 4 | **Fundamental** | Ranks 1–15 only: promoter pledging > 20%, interest cover < 2 (non-financials), pending structural action, listed < 12 months. |
| 5 | **Technical** | Price below the 50-DMA, more than 30% above the 20-DMA, or RSI(14) > 82. |
| 6 | **Risk Caps** | Max 3 per sector, max 4 microcap; fills 8–12 slots. Unfilled slots stay in cash. |
| 7 | **Journal** | Writes the week's permanent record — ranks, exits, buys, strike-outs with cited reasons. |
| 8 | **Monday Plan** | Liquidations first (per ticker: cancel that GTT, then sell), then buys at ~10% with stops at fill × 0.80. Undeployed cash is **parked in a liquid or overnight fund** — all proceeds on a RED week — and the plan says how much to redeem before a GREEN week's buys. |

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

**After tax, against a total-return benchmark (v2.3).** A run settles capital-gains tax
inside the simulation by default — short-term 20%, long-term 12.5% above
₹1.25 lakh a year, parked-cash income at 30%, losses set off and carried
forward — and reports both sides *sold on the last day*, so the strategy's
deferred gains and the benchmark's are taxed alike. Idle cash earns a fixed
6.5% by default, standing in for the liquid fund the live rule parks it in.

The benchmark defaults to the regime index, which is a **price** index. For the
comparison that matters, download the NIFTY MIDSMALLCAP 400 **Total Returns**
history from niftyindices.com (Reports → Historical Data → Total Returns Index)
and save it as `src/backend/app/data/backtest/benchmarks/midsmall400_tri.csv`,
then pick it in the run form. NSE blocks automated downloads, so this one step
is manual. `GET /api/backtest/benchmarks` lists what is available.
The benchmark must cover every weekly mark in the selected window; a run with
missing benchmark weeks fails instead of comparing different periods. Strategy
and benchmark curves begin at the same starting capital and dates. A TRI is a
theoretical index, not an actual fund: expenses and tracking differences remain.

**Stress-testing the frozen plan.** Use the Backtest form to run the same
universe (`midsmall400+microcap250`), dates, formula, regime, capital,
total-return benchmark and tax settings several times, changing only:

| Scenario | Costs per trade | Slippage per fill | Cash yield per year |
|---|---:|---:|---:|
| Baseline | 30 bps | 25 bps | 6.5% |
| Worse execution | 30 bps | 100 bps | 6.5% |
| Higher costs and execution | 100 bps | 100 bps | 6.5% |
| Lower parking yield | 30 bps | 25 bps | 0% |

These are illustrative assumptions, not forecasts or tested limits. Compare
after-exit-tax CAGR, drawdown, turnover and unfilled orders across runs; more
costs need not make every individual path monotonic as trades and sizing change.
Slippage also applies to stop fills. The Weeks view lists orders not filled
because the execution-day open was unavailable. Never substitute a previous
close for an execution price. If an exit cannot be filled, planned buys are
blocked for that week rather than exceeding portfolio caps. Older saved runs
were not recomputed after these fixes and should not be compared as if they were.

**Stated limitations**, reported with every run:

- **Survivorship bias** — a universe is today's index membership applied to all
  of history. Names that were delisted or demoted are absent, which flatters
  returns.
- **The fundamental veto is not simulated** — historical pledging and interest
  coverage are not obtainable. Per the data-safeguard, unverifiable checks pass.
- **Stop fills** use `min(stop, day's open)` less slippage. Daily bars cannot
  determine whether the stop was active when the low occurred, and actual gaps,
  halts, market impact and fills can be worse.
- Prices are yfinance adjusted closes, which differ slightly from NSE prints.
- **Tax uses today's rates for all of history**, without surcharge or cess.
- **A price-index benchmark excludes dividends** (roughly 1% a year), which
  overstates the strategy's lead — flagged on any run that uses one.
- The formula **sweep** is still pre-tax; it ranks formulas against each other,
  it does not estimate what you keep.
- Cached prices may contain vendor-tick repairs using surrounding observations;
  inspect data provenance before treating a run as a strict point-in-time test.

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
py -3.14 -m uvicorn app.main:app --reload

# frontend — http://localhost:5173, proxies /api to :8000
cd src/frontend
npm install
npm run dev
```

Use `py -3.14`, not `python`: on this machine `python` resolves to an older
install without the project's packages.

Tests and the type-generation step:

```powershell
cd src/backend
py -3.14 -m pytest -q                              # 856 tests, ~8 minutes
py -3.14 -m pytest -q --cov=app --cov-report=term-missing   # backend is at 100%
py -3.14 -m pytest -q -rx                          # also lists any recorded caveats (none today)
py -3.14 scripts/export_openapi.py                 # dump the OpenAPI schema

cd src/frontend
npm run gen:api                     # regenerate TS types from the backend OpenAPI
npm test                            # 56 tests (vitest)
npm run test:coverage               # the v2.3 UI is gated at 100%
npm run build
```

Python 3.14, deps in [requirements.txt](requirements.txt).

**Caveats are recorded as tests.** When a test exposes behaviour the Rulebook
would not accept, it is marked `xfail(strict=True)` with the reason, asserting
what the code *should* do; `-rx` lists them, and the suite goes red if one is
fixed without its marker being removed. The 21 recorded on 14 November 2026
were all fixed the next day (Spec v2.4) and are now ordinary regression tests,
each carrying a comment describing the defect it guards against. A few
behaviours are deliberately left as they are and *pinned* by tests named
`test_caveat_...` — for example, the fast sweep fills stops at the week's low
and cannot separate formulas less than about two points apart.

**After changing prices or a universe**, the next formula sweep rebuilds its
feature panel (about half an hour for 400 names): cached panels now carry a
fingerprint of the files they were built from, and panels cached before
15 November 2026 have none, so each is rebuilt once.

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
                            costs, tax, benchmarks, metrics, weekly loop, runs
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
| `backtest/benchmarks/` | downloaded total-return index history (manual) | yes |
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
