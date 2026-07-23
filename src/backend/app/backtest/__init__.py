"""Backtest research module — separate data, shared rules.

Imports the pure strategy core from `app.domain.strategy`, so a run exercises
exactly the rules the live Friday ritual uses. Reads and writes only under
`data/backtest/`; it can never modify the ledger, cycles or journal.
"""
