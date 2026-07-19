"""
app/schemas/activity.py
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ActivityItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    activity_type: str
    reference_id: str | None
    reference_type: str | None
    activity_metadata: dict | None
    created_at: datetime


class ActivityListResponse(BaseModel):
    items: list[ActivityItemResponse]
    total: int
    offset: int
    limit: int


class ActivityTypeCount(BaseModel):
    activity_type: str
    count: int


class ActivitySummaryResponse(BaseModel):
    """Aggregate counts only — never the underlying raw entries. Safe to
    expose broadly (org-wide or branch-wide) since no individual's
    specific actions are identifiable from a total."""
    total_count: int
    by_type: list[ActivityTypeCount]


class UserActivityCountResponse(BaseModel):
    user_id: str
    total_count: int