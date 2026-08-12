"""
app/routers/permissions.py

Grant / revoke individual permissions to org members.

Permission check sequence, matching branches.py / departments.py:
  1. require_permission("member.permission.grant"/"revoke") — flat, cheap
     early exit.
  2. require_same_organization() — tenant isolation.
  3. require_scoped_permission(..., target_branch_id=body.branch_id) —
     authoritative check: can THIS caller grant/revoke at the scope
     they're requesting (org-wide vs this specific branch)? Checked
     BEFORE calling the service, using the scope being acted on, not
     the permission being granted.
  4. Service call — which does its OWN require_scoped_permission ceiling
     check against the permission_name being granted/revoked. Two
     different checks: step 3 asks "can you use the grant/revoke tool at
     this scope at all", step 4 asks "do you have enough authority over
     THIS SPECIFIC permission to hand it out at this scope".

Routes:
  POST   /organizations/{org_id}/members/{user_id}/permissions — grant
  DELETE /organizations/{org_id}/members/{user_id}/permissions — revoke
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    require_permission,
    require_scoped_permission,
    require_same_organization,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.permission import (
    PermissionGrantRequest,
    PermissionGrantResponse,
    PermissionRevokeRequest,
    PermissionRevokeResponse,
    UserPermissionsResponse,   
    PermissionGrantDetail,   
)
from app.services.permission_service import (
    grant_permission,
    revoke_permission,
    get_user_permissions,  
)
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/organizations/{org_id}/members/{user_id}/permissions",
    tags=["permissions"],
)


@router.post(
    "",
    response_model=PermissionGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a permission to a user",
    description=(
        "Grants `permission_name` to the target user. Omit `branch_id` for "
        "an org-wide grant, or provide it to scope the grant to that branch "
        "(only valid for permissions with catalogue scope 'both'). "
        "Requires `member.permission.grant`."
    ),
)
async def grant_permission_route(
    org_id: str,
    user_id: str,
    body: PermissionGrantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.permission.grant")),
) -> PermissionGrantResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/permissions"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="member.permission.grant",
            target_branch_id=body.branch_id,
        )

        grant, replaced_branch_ids = await grant_permission(
            session=db,
            granter=current_user,
            organization_id=org_id,
            target_user_id=user_id,
            permission_name=body.permission_name,
            branch_id=body.branch_id,
        )
        await db.commit()

        message = "Permission granted successfully."
        if replaced_branch_ids:
            message += (
                f" This org-wide grant replaced {len(replaced_branch_ids)} "
                "existing branch-scoped grant(s) for the same permission."
            )

        return PermissionGrantResponse(
            id=grant.id,
            user_id=user_id,
            permission_name=body.permission_name,
            organization_id=org_id,
            branch_id=body.branch_id,
            granted_by=current_user.user_id,
            replaced_branch_grants=replaced_branch_ids,
            message=message,
        )

@router.delete(
    "",
    response_model=PermissionRevokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a permission from a user",
    description=(
        "Revokes an exact permission grant — permission_name + scope must "
        "match the grant exactly (branch_id null for org-wide, or the "
        "specific branch_id). Requires `member.permission.revoke`."
    ),
)
async def revoke_permission_route(
    org_id: str,
    user_id: str,
    body: PermissionRevokeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.permission.revoke")),
) -> PermissionRevokeResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/permissions"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="member.permission.revoke",
            target_branch_id=body.branch_id,
        )

        await revoke_permission(
            session=db,
            revoker=current_user,
            organization_id=org_id,
            target_user_id=user_id,
            permission_name=body.permission_name,
            branch_id=body.branch_id,
        )
        await db.commit()

        return PermissionRevokeResponse(
            user_id=user_id,
            permission_name=body.permission_name,
            branch_id=body.branch_id,
            message="Permission revoked successfully.",
        )

@router.get(
    "",
    response_model=UserPermissionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a user's permissions",
    description=(
        "Returns all permissions granted to the target user, with scope details "
        "(org-wide vs branch-scoped, which branch, who granted it). "
        "Requires `member.role.assign`. "
        "If your grant is org-wide you can see the target's full permission set. "
        "If your grant is branch-scoped you can only see permissions the target "
        "holds that are scoped to your branch(es) or org-wide."
    ),
)
async def get_user_permissions_route(
    org_id: str,
    user_id: str,
    branch_id: str | None = Query(default=None, description="Scope the check to this branch. Omit for an org-wide check."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.role.assign")),
) -> UserPermissionsResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/permissions"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="member.role.assign",
            target_branch_id=branch_id,
        )

        permissions = await get_user_permissions(
            session=db,
            viewer=current_user,
            organization_id=org_id,
            target_user_id=user_id,
        )
        return UserPermissionsResponse(
            user_id=user_id,
            organization_id=org_id,
            total=len(permissions),
            permissions=[PermissionGrantDetail(**p) for p in permissions],
        )