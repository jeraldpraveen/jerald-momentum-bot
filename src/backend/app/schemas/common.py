"""Shared schema primitives: step status + the generic step envelope."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class StepEnvelope(BaseModel, Generic[T]):
    """Uniform wrapper around every workflow step.

    `status`/timestamps are step-agnostic; `output` is typed per step so the
    cycle file stays self-documenting rather than one untyped blob.
    """

    status: StepStatus = StepStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    input: Optional[dict] = None      # e.g. uploaded-file metadata for Part B
    output: Optional[T] = None
    error: Optional[str] = None       # populated when status == FAILED
    notes: Optional[str] = None       # free-text user annotation on review


class StepInfo(BaseModel):
    """Lightweight step descriptor for list/summary views."""

    step_id: str
    status: StepStatus
