"""
app/schemas/dashboard.py
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.challenge import ChallengeResponse
from app.schemas.event import EventResponse
from app.schemas.leaderboard import LeaderboardEntryResponse
from app.schemas.organization import OrganizationMemberInfo
from app.schemas.auth import UserMeResponse
from app.schemas.branch import BranchResponse
from app.schemas.department import DepartmentResponse
class DepartmentBootstrapItem(BaseModel):
    id: str
    name: str
    members: int
    activities: int | None
    rank: int | None
    branch: str | None
    avatars: list[str]


class DashboardBootstrapUserResponse(UserMeResponse):
    """Current user plus organization identity needed during dashboard load."""

    business_name: str
    organization_slug: str


class DashboardBootstrapResponse(BaseModel):
    user: DashboardBootstrapUserResponse
    branches: list[BranchResponse]
    members: list[OrganizationMemberInfo]
    departments: list[DepartmentBootstrapItem]
    events: list[EventResponse]
    challenges: list[ChallengeResponse]
    leaderboard: list[LeaderboardEntryResponse]


class EngagementWellbeingTrendPoint(BaseModel):
    date: date
    stress_level: float | None
    energy_level: float | None
    social_interaction: float | None
    productivity: float | None


class EngagementWellbeingTrendsResponse(BaseModel):
    period: Literal["week", "month", "three_months"]
    start_date: date
    end_date: date
    points: list[EngagementWellbeingTrendPoint]
