"""
app/schemas/organization.py

Pydantic schemas for organization-related endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


# ── Send invite ────────────────────────────────────────────────────────────────

class SendInviteRequest(BaseModel):
    invited_email: EmailStr
    invited_department_id: str = Field(..., min_length=1)
    invited_role_id: str = Field(..., min_length=1)
    branch_id: str | None = Field(
        default=None,
        description="Assign invitee to a specific branch. Omit for org-level.",
    )


class SendInviteResponse(BaseModel):
    invite_id: str
    invited_email: str
    organization_id: str
    branch_id: str | None
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

