# Backtest benchmarks (manual downloads)

Total-return index history for the backtest's benchmark. NSE blocks automated
downloads, so these files are fetched by hand, once.

| File to save here | What it is |
|---|---|
| `midsmall400_tri.csv` | NIFTY MIDSMALLCAP 400 Total Returns Index — the fair comparison |
| `nifty500_tri.csv` | NIFTY 500 Total Returns Index |

How: niftyindices.com → Reports → Historical Data → choose the index and
**Total Returns Index** → pick the full date range → download the CSV.

The file needs a date column and one value column (`Total Returns Index`,
`Close`, `TRI`, `Value` or `NAV`). Extra columns are ignored, day-first dates
are understood, and the rows may be in any order.

`GET /api/backtest/benchmarks` shows which files the app can see and the dates
they cover. Every weekly mark in the chosen backtest window must be present;
choose a shorter common period if either end or an intervening week is missing.
The TRI is theoretical and excludes fund expenses and tracking differences.
