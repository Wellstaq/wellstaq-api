"""
app/schemas/organization.py

Pydantic schemas for organization-related endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ── Send invite ────────────────────────────────────────────────────────────────

class SendInviteRequest(BaseModel):
    invited_email: EmailStr
    invited_department_id: str = Field(..., min_length=1)
    invited_role_id: str = Field(..., min_length=1)
    branch_id: str = Field(
        ...,
        min_length=1,
        description="Branch to assign the invitee to. Required — every invite must target a branch.",
    )


class SendInviteResponse(BaseModel):
    invite_id: str
    invited_email: str
    organization_id: str
    branch_id: str
    invited_department_id: str
    invited_role_id: str
    status: str
    expires_at: str


# ── List invites ───────────────────────────────────────────────────────────────

class InviteItem(BaseModel):
    invite_id: str
    invited_email: str
    organization_id: str
    branch_id: str | None
    department_id: str | None = None
    role_name: str | None = None
    invited_department_id: str
    invited_role_id: str
    invited_by: str
    status: str
    expires_at: str
    created_at: str

    model_config = {"from_attributes": True}


class InviteListResponse(BaseModel):
    items: list[InviteItem]
    total: int
    offset: int
    limit: int


# ── Members ──────────────────────────────────────────────────────────────────

class OrganizationMemberInfo(BaseModel):
    """Allow-list — deliberately excludes hashed_password and other
    sensitive User fields. Add fields only as the client actually needs."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str
    last_name: str
    email: str
    avatar_url: str | None
    status: str
    role_id: str | None
    branch_id: str | None


class OrganizationMembersListResponse(BaseModel):
    items: list[OrganizationMemberInfo]
    total: int
    offset: int
    limit: int


# ── Clubs ────────────────────────────────────────────────────────────────────

class OrganizationClubInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    privacy: str
    category: str
    branch_id: str | None
    member_count: int


class OrganizationClubsListResponse(BaseModel):
    items: list[OrganizationClubInfo]
    total: int
    offset: int
    limit: int


# ── Org / branch stats ────────────────────────────────────────────────────────
class TrendData(BaseModel):
    current: int
    previous: int | None  # None when no baseline exists (Overall, or custom range with no prior data)
    change_pct: float | None
    
class OrgStatsResponse(BaseModel):
    total_users: int
    users_trend: TrendData
    total_branches: int
    branches_trend: TrendData
    total_departments: int
    departments_trend: TrendData
    total_clubs: int
    clubs_trend: TrendData
    total_events: int
    events_trend: TrendData
    total_challenges: int
    challenges_trend: TrendData
    active_challenges: int
    total_posts: int
    posts_trend: TrendData

class BranchStatsResponse(BaseModel):
    total_users: int
    users_trend: TrendData
    total_departments: int
    departments_trend: TrendData
    total_clubs: int
    clubs_trend: TrendData
    total_events: int
    events_trend: TrendData
    total_challenges: int
    challenges_trend: TrendData
