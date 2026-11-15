"""Domain-level exceptions. Routers translate these into HTTP status codes."""

from __future__ import annotations


class DomainError(Exception):
    """Base for service-layer errors carrying a machine code + message."""

    code = "domain_error"
    http_status = 400

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.context}


class NoActiveCycle(DomainError):
    code = "no_active_cycle"
    http_status = 404


class CycleConflict(DomainError):
    """A new cycle was requested while one is still open."""

    code = "cycle_already_open"
    http_status = 409


class CycleClosed(DomainError):
    """A write was attempted against a closed (read-only) cycle."""

    code = "cycle_closed"
    http_status = 409


class StepNotReady(DomainError):
    """Advance was requested before the step's run completed."""

    code = "step_not_ready"
    http_status = 409


class FormulaFrozen(DomainError):
    """The live ritual was asked to score with a formula other than the frozen one."""

    code = "formula_frozen"
    http_status = 409


class CycleNotFound(DomainError):
    code = "cycle_not_found"
    http_status = 404
