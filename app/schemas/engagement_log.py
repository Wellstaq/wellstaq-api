"""
app/schemas/engagement_log.py
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel



class EngagementTrendPoint(BaseModel):
    date: date
    value: Decimal
    previous_value: Decimal | None
    trend_direction: str | None


class EngagementSnapshotResponse(BaseModel):
    date: date | None
    checkins: Decimal | None
    checkins_trend: str | None
    participation: Decimal | None
    participation_trend: str | None
    social: Decimal | None
    social_trend: str | None
    productivity: Decimal | None
    productivity_trend: str | None


class EngagementTrendResponse(BaseModel):
    metric_type: str
    points: list[EngagementTrendPoint]

