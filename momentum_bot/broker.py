"""Broker abstraction. Part A only needs `cancel_all_gtts` (RED regime).

DryRunBroker is the default and never touches a real account; it just records
what it would have done so the action lands in the journal. Wire a real
implementation (e.g. Zerodha Kite Connect) behind the same interface later.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class Broker(ABC):
    @abstractmethod
    def cancel_all_gtts(self) -> list[str]:
        """Cancel every resting GTT order. Returns identifiers of cancelled orders."""


class DryRunBroker(Broker):
    def __init__(self) -> None:
        self.actions: list[str] = []

    def cancel_all_gtts(self) -> list[str]:
        self.actions.append("CANCEL_ALL_GTTS (dry run — no broker connected)")
        log.warning("DRY RUN: would cancel all resting GTT orders at the broker")
        return []


class KiteBroker(Broker):
    """Placeholder for Zerodha Kite Connect. Implement before going live.

    kite.get_gtts() -> [g["id"]...] -> kite.delete_gtt(id) for each.
    """

    def __init__(self, api_key: str, access_token: str) -> None:
        raise NotImplementedError("Live broker integration not wired up yet")

    def cancel_all_gtts(self) -> list[str]:
        raise NotImplementedError
