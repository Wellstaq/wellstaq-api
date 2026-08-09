"""
app/schemas/dashboard.py
"""

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


class DashboardBootstrapResponse(BaseModel):
    user: UserMeResponse
    branches: list[BranchResponse]
    members: list[OrganizationMemberInfo]
    departments: list[DepartmentBootstrapItem]
    events: list[EventResponse]
    challenges: list[ChallengeResponse]
    leaderboard: list[LeaderboardEntryResponse]