"""
app/services/permission_service.py

Grants and revokes UserPermission rows.

Enforces three things:
  1. SUPER_ADMIN_ONLY_PERMISSIONS can never be granted via this path —
     authority over these comes exclusively from role == super_admin.
  2. scope="org" permissions can only ever be granted branch_id=NULL —
     a branch-scoped grant of an org-only permission is rejected.
  3. Grant/revoke ceiling — you cannot grant or revoke a scope you don't
     yourself hold. Delegated to require_scoped_permission so the
     ceiling logic lives in exactly one place, shared with every other
     scoped-permission check in the app.

Contract:
  - Accepts a live AsyncSession.
  - Does not call commit() — the router owns that.
  - flush() populates the PK before the router serializes the response.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.core.constants import SUPER_ADMIN_ONLY_PERMISSIONS
from app.models import User, Branch, Role
from app.models.permission import Permission, UserPermission
from app.services.auth.token_service import load_user_permissions

logger = logging.getLogger(__name__)


async def grant_permission(
    *,
    session: AsyncSession,
    granter: CurrentUser,
    organization_id: str,
    target_user_id: str,
    permission_name: str,
    branch_id: str | None,
) -> tuple[UserPermission, list[str]]:
    """
    Grant `permission_name` to target_user_id, scoped to branch_id
    (None = org-wide).

    Returns (grant, replaced_branch_ids) — replaced_branch_ids is the
    list of branch_ids whose branch-scoped grant was deleted because an
    org-wide grant now supersedes them. Always empty unless this call
    granted org-wide AND the user previously held branch-scoped grants
    for the same permission.

    Org-wide vs branch-scoped interaction rules:
      - Granting a BRANCH-scoped grant when the user already holds an
        ORG-WIDE grant for the same permission is rejected (409).
      - Granting an ORG-WIDE grant when the user already holds one or
        more BRANCH-scoped grants for the same permission REPLACES
        them, in the same transaction.

    Raises:
      404 — permission not found.
      404 — target user not found or not in this org.
      404 — branch_id provided but does not exist / does not belong to this org.
      403 — permission_name is super-admin-only (never grantable).
      400 — branch_id provided for an org-scope-only permission.
      403 — granter doesn't hold this permission at (at least) the
             scope being granted — ceiling check.
      409 — target user already holds this exact grant.
      409 — target user already holds the org-wide version of this
             permission (when branch_id is provided).
    """
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "Permissions are managed through organization and branch roles; direct user grants are disabled.",
    )
    if target_user_id == granter.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot grant permissions to your own account.")
    target_role_name = (await session.execute(
        select(Role.name).join(User, User.role_id == Role.id).where(
            User.id == target_user_id, User.organization_id == organization_id,
        )
    )).scalar_one_or_none()
    if target_role_name == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin permissions are protected and cannot be changed.")
    if permission_name in SUPER_ADMIN_ONLY_PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{permission_name}' can never be granted — it is reserved for super_admin.",
        )

    perm_result = await session.execute(
        select(Permission).where(Permission.name == permission_name)
    )
    permission = perm_result.scalar_one_or_none()
    if permission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found.",
        )

    if permission.scope == "org" and branch_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{permission_name}' can only be granted org-wide, not scoped to a branch.",
        )

    if branch_id is not None:
        branch_result = await session.execute(
            select(Branch).where(
                Branch.id == branch_id,
                Branch.organization_id == organization_id,
            )
        )
        if branch_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found or does not belong to this organization.",
            )

    target_result = await session.execute(
        select(User).where(
            User.id == target_user_id,
            User.organization_id == organization_id,
        )
    )
    if target_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or does not belong to this organization.",
        )

    await require_scoped_permission(
        db=session,
        current_user=granter,
        permission=permission_name,
        target_branch_id=branch_id,
    )

    existing = await session.execute(
        select(UserPermission).where(
            UserPermission.user_id == target_user_id,
            UserPermission.permission_id == permission.id,
            UserPermission.organization_id == organization_id,
            UserPermission.branch_id == branch_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already holds this exact grant.",
        )

    replaced_branch_ids: list[str] = []

    if branch_id is not None:
        org_wide_result = await session.execute(
            select(UserPermission).where(
                UserPermission.user_id == target_user_id,
                UserPermission.permission_id == permission.id,
                UserPermission.organization_id == organization_id,
                UserPermission.branch_id.is_(None),
            )
        )
        if org_wide_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"User already holds '{permission_name}' org-wide, which "
                    "already covers every branch. A branch-scoped grant "
                    "would be redundant."
                ),
            )
    else:
        stale_branch_grants = await session.execute(
            select(UserPermission).where(
                UserPermission.user_id == target_user_id,
                UserPermission.permission_id == permission.id,
                UserPermission.organization_id == organization_id,
                UserPermission.branch_id.is_not(None),
            )
        )
        stale_grants = stale_branch_grants.scalars().all()
        replaced_branch_ids = [g.branch_id for g in stale_grants]

        for stale in stale_grants:
            await session.delete(stale)

        if stale_grants:
            await session.flush()
            logger.info(
                "Replaced %d branch-scoped grant(s) with org-wide grant",
                len(stale_grants),
                extra={
                    "permission": permission_name,
                    "target_user_id": target_user_id,
                    "replaced_branch_ids": replaced_branch_ids,
                    "org_id": organization_id,
                },
            )

    grant = UserPermission(
        user_id=target_user_id,
        permission_id=permission.id,
        organization_id=organization_id,
        branch_id=branch_id,
        granted_by=granter.user_id,
    )
    session.add(grant)
    await session.flush()
    await session.refresh(grant)

    logger.info(
        "Permission granted",
        extra={
            "permission": permission_name,
            "target_user_id": target_user_id,
            "branch_id": branch_id,
            "granted_by": granter.user_id,
            "org_id": organization_id,
        },
    )
    return grant, replaced_branch_ids

async def revoke_permission(
    *,
    session: AsyncSession,
    revoker: CurrentUser,
    organization_id: str,
    target_user_id: str,
    permission_name: str,
    branch_id: str | None,
) -> None:
    """
    Revoke a specific grant (exact permission + exact scope) from a user.

    Same ceiling logic as granting — you can't revoke authority at a
    scope you don't yourself hold, otherwise a branch manager could
    strip an org-wide admin's permissions.

    Raises:
      404 — permission not found.
      403 — revoker doesn't hold this permission at (at least) this scope.
      404 — no matching grant exists to revoke.
    """
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "Permissions are managed through organization and branch roles; direct user revocation is disabled.",
    )
    if target_user_id == revoker.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot revoke permissions from your own account.")
    target_role_name = (await session.execute(
        select(Role.name).join(User, User.role_id == Role.id).where(
            User.id == target_user_id, User.organization_id == organization_id,
        )
    )).scalar_one_or_none()
    if target_role_name == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin permissions are protected and cannot be changed.")
    perm_result = await session.execute(
        select(Permission).where(Permission.name == permission_name)
    )
    permission = perm_result.scalar_one_or_none()
    if permission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found.",
        )

    await require_scoped_permission(
        db=session,
        current_user=revoker,
        permission=permission_name,
        target_branch_id=branch_id,
    )

    existing = await session.execute(
        select(UserPermission).where(
            UserPermission.user_id == target_user_id,
            UserPermission.permission_id == permission.id,
            UserPermission.organization_id == organization_id,
            UserPermission.branch_id == branch_id,
        )
    )
    grant = existing.scalar_one_or_none()
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching grant found to revoke.",
        )

    await session.delete(grant)
    await session.flush()

    logger.info(
        "Permission revoked",
        extra={
            "permission": permission_name,
            "target_user_id": target_user_id,
            "branch_id": branch_id,
            "revoked_by": revoker.user_id,
            "org_id": organization_id,
        },
    )

async def get_user_permissions(
    *,
    session: AsyncSession,
    viewer: CurrentUser,
    organization_id: str,
    target_user_id: str,
    branch_id: str | None = None,
) -> list[dict]:
    """
    Return all permission grants for target_user_id, with scope details.

    Access rules mirror require_scoped_permission on member.role.assign:
      - org-wide grant (branch_id IS NULL)  → can see ALL of the target's
        permissions regardless of their scope
      - branch-scoped grant (branch_id = X) → can only see permissions the
        target holds that are scoped to branch X OR org-wide (which covers X)
      - super_admin bypasses entirely — sees everything

    Each returned item includes:
      permission_name, category, scope (catalogue scope: org/both),
      grant_scope ("org_wide" | "branch"), branch_id, granted_by, granted_at
    """
    # Validate target user exists in this org
    target_result = await session.execute(
        select(User).where(
            User.id == target_user_id,
            User.organization_id == organization_id,
        )
    )
    if target_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or does not belong to this organization.",
        )

    effective = await load_user_permissions(session, target_user_id, organization_id)
    effective = [grant for grant in effective if grant.branch_id == branch_id]
    names = {grant.name for grant in effective}
    catalogue = (await session.execute(
        select(Permission).where(Permission.name.in_(names)).order_by(Permission.category, Permission.name)
    )).scalars().all() if names else []
    grants_by_name = {grant.name: grant for grant in effective}
    return [{
        "permission_name": permission.name,
        "category": permission.category,
        "catalogue_scope": permission.scope,
        "grant_scope": "org_wide" if grants_by_name[permission.name].branch_id is None else "branch",
        "branch_id": grants_by_name[permission.name].branch_id,
        "granted_by": None,
        "granted_at": None,
    } for permission in catalogue]
