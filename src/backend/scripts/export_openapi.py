"""Dump the FastAPI OpenAPI schema to src/shared/openapi.json.

No running server needed. Run from src/backend:  python scripts/export_openapi.py
The frontend's `npm run gen:api` turns this into TS types.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "shared" / "openapi.json"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
