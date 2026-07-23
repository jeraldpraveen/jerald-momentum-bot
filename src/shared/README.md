# shared — cross-language type contract

Frontend (TypeScript) and backend (Python/Pydantic) can't share source files, so
the backend's OpenAPI schema is the single source of truth for API types.

## Workflow

1. Backend regenerates the schema after any Pydantic change:
   ```
   cd ../backend && python scripts/export_openapi.py   # writes ./openapi.json
   ```
2. Frontend generates TS types from it:
   ```
   cd ../frontend && npm run gen:api                   # writes ./generated/schema.d.ts
   ```

`openapi.json` and `generated/schema.d.ts` are **checked in** so the frontend
type-checks without the backend running, and schema changes show up in diffs.

## Current state (Part A slice)

The frontend uses hand-written types in `frontend/src/api/types.ts` for now —
fine while the surface is small. Switch imports to `generated/schema.d.ts` once
Part B adds enough endpoints that hand-sync becomes error-prone.
