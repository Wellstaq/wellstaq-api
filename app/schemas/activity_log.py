"""
app/schemas/activity_log.py
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AUTOMATIC_ACTIVITY_METRICS = frozenset({"steps", "run", "calories"})


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
    source: str
    is_verified: bool


class ActivitySubmitResponse(BaseModel):
    entry: ActivityLogResponse
    challenges_updated: list[str] = Field(
        default_factory=list, description="IDs of active challenges this submission also fed progress into."
    )


class HealthActivityRecord(BaseModel):
    record_id: str = Field(..., min_length=1, max_length=200)
    metric_type: Literal["steps", "run", "calories"]
    value: Decimal = Field(..., ge=0)
    recorded_date: date
    started_at: datetime
    ended_at: datetime
    recording_method: Literal["automatic", "active"]
    source_name: str | None = Field(default=None, max_length=120)
    source_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_record(self):
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be on or after started_at")
        maximums = {"steps": Decimal("200000"), "run": Decimal("250"), "calories": Decimal("20000")}
        if self.value > maximums[self.metric_type]:
            raise ValueError(f"{self.metric_type} value exceeds the supported daily maximum")
        return self


class HealthActivitySyncRequest(BaseModel):
    provider: Literal["apple_health", "health_connect"]
    device_id: str = Field(..., min_length=1, max_length=128)
    records: list[HealthActivityRecord] = Field(..., min_length=1, max_length=93)

    @model_validator(mode="after")
    def unique_daily_metrics(self):
        keys = [(record.metric_type, record.recorded_date) for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("Only one aggregate per metric and date can be synced")
        return self


class HealthActivitySyncResponse(BaseModel):
    entries: list[ActivityLogResponse]
    challenges_updated: list[str] = Field(default_factory=list)


class ActivityTrendPoint(BaseModel):
    period: date
    value: Decimal


class ActivityTrendResponse(BaseModel):
    metric_type: str
    granularity: str
    points: list[ActivityTrendPoint]
