# Backtest & ML Research Module

Research tool (decoupled from the live app) for backtesting the momentum
rotation strategy and training an ML stock-selection probability model. See the
roadmap in `.claude/plans/` for the full phase plan.

## Status

- **Phase 1 (started)** — the Jerald Score (momentum ranking, modeled on the
  Sigma Scanner but on correct windows/data) is computed in code (no Excel) as a
  shared strategy primitive at
  **`src/backend/app/domain/momentum_score.py`** (next to `supertrend.py` /
  `indicators.py`), so both this backtest and the live app's Part B import it —
  the app never depends on the research module. Parameterized risk-adjusted
  momentum score: `(w6·R6m + w3·R3m + w1·R1m) / (Vol3m + Vol1m)`, base weights
  0.6/0.3/0.1. Every knob (weights, lookbacks, vol windows) is tunable — the
  "find the best scoring mechanism" search space. Backtest code imports it via
  `from app.domain.momentum_score import ...` (add `src/backend` to the path).

## Run tests

The momentum-score tests live with the backend suite:

```
cd src/backend && python -m pytest -q    # includes test_momentum_score.py
```

Backtest-specific tests will live in `src/backtest/tests/` as phases land.

## Data sources (verified)

- **Per-stock OHLCV**: yfinance `<SYMBOL>.NS`, `period="max"` (cache as parquet).
- **Universe (constituents)**: NSE archives CSV, e.g.
  `https://archives.nseindia.com/content/indices/ind_niftymidsmallcap400list.csv`
  — columns Company Name, Industry (→ sector cap), Symbol (→ `<Symbol>.NS`), ISIN.
  Microcap 250: `ind_niftymicrocap250list.csv`. Note: current members only
  (survivorship bias for history — documented limitation).
- **Index history** (regime): reuse `app/config.INDEX_OPTIONS` tickers via yfinance.

## Layout (as it grows)

```
app/domain/momentum_score.py  Jerald Score (DONE)   data/     price cache + universe
features/  engineer, RSI…   model/  train/predict   engine/   backtester, portfolio
labeling.py  forward labels metrics.py  perf stats  cli.py    run a config
```
