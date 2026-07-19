"""
app/schemas/engagement.py
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Priorities ───────────────────────────────────────────────────────────────

class PrioritySetRequest(BaseModel):
    """Full replacement of the caller's priority list, in rank order.
    Item 0 = rank 1 (highest). Replacing rather than patching avoids
    partial-update ambiguity around rank gaps/dupes."""
    priorities: list[str] = Field(..., min_length=1, max_length=10)


class PriorityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    priority: str
    rank: int


class PriorityListResponse(BaseModel):
    items: list[PriorityResponse]


# ── Daily check-in ───────────────────────────────────────────────────────────

class CheckinSubmitRequest(BaseModel):
    mood: str = Field(..., description="good | bad | stressed | tired")
    energy_level: str = Field(..., description="good | bad | stressed | tired")
    stress_level: str = Field(..., description="good | bad | stressed | tired")
    notes: str | None = Field(default=None, max_length=1000)


class CheckinResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    mood: str
    energy_level: str
    stress_level: str
    notes: str | None
    checked_in_at: datetime
    checkin_date: date


class CheckinListResponse(BaseModel):
    items: list[CheckinResponse]
    total: int
    offset: int
    limit: int


class CheckinStreakResponse(BaseModel):
    current_streak_days: int
    checked_in_today: bool