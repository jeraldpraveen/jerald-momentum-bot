"""Make the `app` package importable when running pytest from src/backend."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
