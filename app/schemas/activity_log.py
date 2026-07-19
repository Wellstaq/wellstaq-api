"""
app/schemas/activity_log.py
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ActivitySubmitRequest(BaseModel):
    metric_type: str = Field(..., description="e.g. steps, distance, run, 7min_workout, calories, manual")
    value: Decimal = Field(..., ge=0)
    unit: str | None = Field(default=None, max_length=20, description="e.g. km, reps, mins — steps has no unit")
    source: str = Field(default="manual", max_length=30)


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    metric_type: str
    value: Decimal
    unit: str | None
    date: date


class ActivitySubmitResponse(BaseModel):
    entry: ActivityLogResponse
    challenges_updated: list[str] = Field(
        default_factory=list, description="IDs of active challenges this submission also fed progress into."
    )


class ActivityTrendPoint(BaseModel):
    period: date
    value: Decimal


class ActivityTrendResponse(BaseModel):
    metric_type: str
    granularity: str
    points: list[ActivityTrendPoint]