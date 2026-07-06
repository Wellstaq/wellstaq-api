"""
app/schemas/permission.py

Pydantic schemas for granting/revoking UserPermission rows.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime



class PermissionGrantRequest(BaseModel):
    permission_name: str = Field(..., min_length=1, description="e.g. 'branch.update'")
    branch_id: str | None = Field(
        default=None,
        description=(
            "Omit or null for an org-wide grant. Provide a branch_id to "
            "scope the grant to that branch. Only valid if the permission's "
            "catalogue scope is 'both' — 'org'-scope permissions reject a "
            "non-null branch_id."
        ),
    )


class PermissionGrantResponse(BaseModel):
    id: str
    user_id: str
    permission_name: str
    organization_id: str
    branch_id: str | None
    granted_by: str
    replaced_branch_grants: list[str] = Field(
        default_factory=list,
        description=(
            "Branch IDs whose branch-scoped grant for this permission was "
            "removed because this org-wide grant now supersedes them. "
            "Empty if this wasn't an org-wide grant, or the user held no "
            "branch-scoped grants to replace."
        ),
    )
    message: str
class PermissionRevokeRequest(BaseModel):
    permission_name: str = Field(..., min_length=1)
    branch_id: str | None = Field(
        default=None,
        description="Must match the exact grant being revoked — None revokes the org-wide grant, a branch_id revokes only that branch's grant.",
    )


class PermissionRevokeResponse(BaseModel):
    user_id: str
    permission_name: str
    branch_id: str | None
    message: str

class PermissionGrantDetail(BaseModel):
    """Single permission grant with full scope detail."""
    permission_name: str
    category: str
    catalogue_scope: str      # "org" | "both" — from Permission catalogue
    grant_scope: str          # "org_wide" | "branch" — how this grant is scoped
    branch_id: str | None
    granted_by: str | None
    granted_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class UserPermissionsResponse(BaseModel):
    """All permissions held by a user, with scope details."""
    user_id: str
    organization_id: str
    total: int
    permissions: list[PermissionGrantDetail]