"""Request body schemas."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel


class CreateCycleRequest(BaseModel):
    # Optional override; defaults to the most recent Friday (server-side).
    week_end: Optional[dt.date] = None


class StepNotes(BaseModel):
    notes: str
