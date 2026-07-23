"""Path setup so the backtest can import both its own package and the backend's
shared strategy primitives (app.domain.*, app.integrations.*)."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent        # src/
_BACKEND = _SRC / "backend"                          # src/backend  (for `app.*`)

for p in (str(_SRC), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)
