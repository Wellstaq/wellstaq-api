"""
app/schemas/branch.py

Pydantic schemas for branch-related endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── Create ───────────────────────────────────────────────────────────────────

class BranchCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
  
# ── Update ───────────────────────────────────────────────────────────────────
class BranchUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)

# ── Response ─────────────────────────────────────────────────────────────────

class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    name: str
    manager_id: str | None
    created_by: str | None


class BranchListResponse(BaseModel):
    items: list[BranchResponse]
    total: int
    offset: int
    limit: int


# ── Members ──────────────────────────────────────────────────────────────────

class BranchMemberInfo(BaseModel):
    """Allow-list — deliberately excludes hashed_password and other
    sensitive User fields. Add fields only as the client actually needs."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str
    last_name: str
    email: str
    avatar_url: str | None
    status: str


class BranchMembersListResponse(BaseModel):
    items: list[BranchMemberInfo]
    total: int
    offset: int
    limit: int

class BranchMemberAssignRequest(BaseModel):
    department_id: str = Field(..., min_length=1)
    
class BranchMemberAssignResponse(BaseModel):
    user_id: str
    branch_id: str
    organization_id: str
    message: str


# ── Delete ───────────────────────────────────────────────────────────────────

class BranchDeleteRequest(BaseModel):
    target_branch_id: str | None = Field(
        default=None,
        description=(
            "Required if the branch has members. All members are "
            "transferred here before the branch is deleted."
        ),
    )

# ── Manager assignment ──────────────────────────────────────────────────────

class BranchManagerAssignRequest(BaseModel):
    manager_id: str = Field(..., min_length=1)


class BranchManagerResponse(BaseModel):
    branch_id: str
    organization_id: str
    manager_id: str | None
    message: str