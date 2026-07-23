"""Unified strategy journal.

Two sinks per Friday ritual:
  * data/journal.jsonl — full machine-readable state (one JSON object per run)
  * data/journal.md    — human-readable table row, mirroring the source-of-truth doc
"""

from __future__ import annotations

import json

from . import config

MD_HEADER = (
    "# Strategy Journal\n\n"
    "| Run (IST) | Week End | Regime | Close | Supertrend | Source | Actions |\n"
    "| :-- | :-- | :-- | --: | --: | :-- | :-- |\n"
)


def write_journal(entry: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(config.JOURNAL_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    if not config.JOURNAL_MD.exists():
        config.JOURNAL_MD.write_text(MD_HEADER, encoding="utf-8")
    actions = "; ".join(entry.get("actions", [])) or "—"
    row = (
        f"| {entry['run_at']} | {entry['week_end']} | **{entry['regime']}** "
        f"| {entry['index_close']:.2f} | {entry['supertrend']:.2f} "
        f"| {entry['data_source']} | {actions} |\n"
    )
    with open(config.JOURNAL_MD, "a", encoding="utf-8") as f:
        f.write(row)
