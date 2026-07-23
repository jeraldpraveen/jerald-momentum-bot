# Backend — Momentum Rotation Workbench (FastAPI)

Weekly, manually-driven momentum strategy. A **decision + journaling workbench** —
it computes decisions and produces plans; it does NOT monitor the market or place
broker orders. Layered: `routers/` (thin) → `services/` (all logic) →
`repositories/` (only disk I/O).

## Layout

```
app/
  config.py          frozen strategy params + paths
  domain/            pure math (supertrend.py)
  integrations/      read-only market data (market_data.py)
  schemas/           Pydantic models (OpenAPI source of truth)
  repositories/      cycles (JSON-per-week + pointer), ledger (CSV)
  services/          cycle_service (state machine), regime_gate_service (Part A)
  routers/           cycles, regime_gate, stubs (501 for unbuilt steps)
  main.py            app factory, CORS, error handlers
  data/              cycles/<id>.json, _current_cycle.json, holdings_ledger.csv
scripts/export_openapi.py   dump schema → ../shared/openapi.json
tests/               pytest (unit + API integration)
```

## Run

```
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload   # http://127.0.0.1:8000  (docs at /docs)
python -m pytest                          # from src/backend
python scripts/export_openapi.py          # regenerate shared OpenAPI schema
```

Note (Windows PowerShell): use `python -m uvicorn` (the `uvicorn.exe` shim may
not be on PATH), and run commands one per line — PowerShell 5.1 has no `&&`.

## Part A flow (built)

1. `POST /api/cycles` — start the week's cycle (409 if one is already open).
2. `POST /api/cycles/current/steps/part-a/run` — fetch NIFTY MIDSMALLCAP 400
   weekly candles, compute Supertrend(1, 2.5), decide RED/GREEN. Replaces the
   manual TradingView check. Data-provider failure → step `failed` + error (HTTP 200).
3. Review the result, then `POST /api/cycles/current/steps/part-a/advance` —
   GREEN → Part B; RED → marks holdings for exit, instructs manual GTT cancel,
   short-circuits to the Phase 3 exit checklist.

Everything after Part A (B/C/D/E, Phase 1 event log, Phase 3 plan) is a 501 stub,
built incrementally. Note Part D's veto matrix is **7-point** (v2.1 spec).
