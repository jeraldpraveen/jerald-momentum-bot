"""Entry point for Part A — the Friday-close Regime Gate Evaluation.

Usage:
    python run_part_a.py            # dry run (no broker calls), evaluates the gate
"""

import logging

from momentum_bot.broker import DryRunBroker
from momentum_bot.regime_gate import run_regime_gate

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    entry = run_regime_gate(broker=DryRunBroker())

    print("\n=== REGIME GATE RESULT ===")
    print(f"Week ending      : {entry['week_end']}")
    print(f"Index close      : {entry['index_close']}")
    print(f"Supertrend (1,2.5): {entry['supertrend']}")
    print(f"SYSTEM_REGIME    : {entry['regime']}  (previous: {entry['previous_regime'] or 'n/a'})")
    print(f"Data source      : {entry['data_source']}")
    if entry["reentry_required"]:
        print("RED->GREEN shift : MANDATORY re-entry Monday open")
    for a in entry["actions"]:
        print(f"  - {a}")
    # Exit code signals the branch to any wrapping scheduler: 0 = GREEN (continue
    # to Part B), 2 = RED (pipeline terminates here by design).
    return 0 if entry["regime"] == "GREEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
