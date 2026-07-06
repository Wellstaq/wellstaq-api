"""
app/services/branch/update_branch.py

Update operations for branches.

  edit_branch                        — patch name
  assign_user_to_branch              — assign/transfer a user into a branch
  assign_branch_manager              — assign a branch's manager
  unassign_branch_manager            — remove a branch's manager
  seed_role_permissions_for_branch   — grant a user the branch-usable
                                        permissions for a specific branch
  unseed_role_permissions_for_branch — revoke exactly what was seeded
                                        for a specific branch

Branch membership is a single FK (User.branch_id) rather than a join
table — a user can only ever have ONE branch_id value at the DB level,
so "a user must not belong to multiple branches" is enforced by the
column itself. assign_user_to_branch's job is: validate everything,
then overwrite that FK, transferring the user out of whatever branch
they were previously in (if any) implicitly.

Headquarters rename protection:
  edit_branch() rejects name changes when branch.is_headquarters=True.
  The name "Headquarters" must remain stable so other parts of the
  system (and org admins) can rely on it as the canonical label for the
  org-wide home branch. This also prevents a collision where an admin
  renames HQ to something else and then a new regular branch is created
  named "Headquarters," breaking the semantic guarantee.

Permission seeding for branch managers:
  - Permission.scope only ever takes two values: 'org' or 'both'.
    'org' permissions only make sense org-wide and are never handed
    to a branch manager automatically. 'both' permissions are usable
    at either level — exactly the set a branch manager needs.
  - When a user becomes a branch's manager (via assign_branch_manager
    below, or via invite registration), they are automatically granted
    every scope='both' permission scoped to that specific branch.
  - seed_role_permissions_for_branch() is the single shared
    implementation used by both call paths so they can never drift.
  - A user can manage multiple branches — each assignment seeds its
    own branch-scoped copy of the permission list. Unassigning from
    one branch only removes that branch's rows; the others are untouched.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Permission, User, UserPermission

logger = logging.getLogger(__name__)

SEEDED_PERMISSION_SCOPE = "both"


async def seed_role_permissions_for_branch(
    session: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
    branch_id: str,
) -> None:
    """
    Grant a user every scope='both' permission, scoped to this specific
    branch. Used whenever a user becomes a branch's governing role.

    Idempotent: skips any permission the user already holds scoped to
    this exact branch, or org-wide (branch_id=NULL, which supersedes
    a branch-level grant). Safe to call again on manager reassignment.
    """
    perms_result = await session.execute(
        select(Permission).where(Permission.scope == SEEDED_PERMISSION_SCOPE)
    )
    permissions = perms_result.scalars().all()
    if not permissions:
        return

    existing_result = await session.execute(
        select(UserPermission.permission_id, UserPermission.branch_id).where(
            UserPermission.user_id == user_id,
            UserPermission.organization_id == organization_id,
        )
    )
    existing_rows = existing_result.all()

    covered_permission_ids = {
        perm_id for perm_id, b_id in existing_rows
        if b_id is None or b_id == branch_id
    }

    new_grants = [
        UserPermission(
            user_id=user_id,
            organization_id=organization_id,
            permission_id=perm.id,
            branch_id=branch_id,
        )
        for perm in permissions
        if perm.id not in covered_permission_ids
    ]

    if new_grants:
        session.add_all(new_grants)
        await session.flush()

    logger.info(
        "Seeded branch-level permissions",
        extra={
            "user_id": user_id,
            "branch_id": branch_id,
            "organization_id": organization_id,
            "granted_count": len(new_grants),
            "skipped_existing": len(permissions) - len(new_grants),
        },
    )


async def unseed_role_permissions_for_branch(
    session: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
    branch_id: str,
) -> None:
    """
    Revoke exactly what seed_role_permissions_for_branch() granted for
    THIS branch. Only touches this branch's scope='both' rows —
    other branches and org-wide grants are untouched.
    """
    perm_ids_subquery = select(Permission.id).where(
        Permission.scope == SEEDED_PERMISSION_SCOPE
    )

    delete_stmt = delete(UserPermission).where(
        UserPermission.user_id == user_id,
        UserPermission.organization_id == organization_id,
        UserPermission.branch_id == branch_id,
        UserPermission.permission_id.in_(perm_ids_subquery),
    )
    result = await session.execute(delete_stmt)
    await session.flush()

    logger.info(
        "Unseeded branch-level permissions",
        extra={
            "user_id": user_id,
            "branch_id": branch_id,
            "organization_id": organization_id,
            "revoked_count": result.rowcount,
        },
    )


async def edit_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str | None,
) -> Branch:
    """
    Partially update a branch's editable fields.

    The Headquarters branch cannot be renamed — its name is a stable
    system identifier. Attempting to rename it raises 403.

    manager_id is NOT handled here — it has its own dedicated endpoints
    (assign_branch_manager / unassign_branch_manager) so there is exactly
    one path that mutates it, with its own validation.

    Raises:
      404 — branch not found or belongs to a different org.
      403 — branch is the Headquarters branch (name is immutable).
      409 — new name conflicts with an existing branch (case-insensitive).
    """
    result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found or does not belong to this organization.",
        )

    if branch.is_headquarters:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The Headquarters branch cannot be renamed.",
        )

    if name is not None:
        name = name.strip()
        if name.lower() != branch.name.lower():
            duplicate = await session.execute(
                select(Branch).where(
                    Branch.organization_id == organization_id,
                    Branch.id != branch_id,
                    func.lower(Branch.name) == name.lower(),
                )
            )
            if duplicate.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A branch named '{name}' already exists in this organization.",
                )
        branch.name = name

    await session.flush()
    await session.refresh(branch)

    logger.info(
        "Branch updated",
        extra={"branch_id": branch_id, "org_id": organization_id},
    )
    return branch


async def assign_user_to_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    user_id: str,
) -> User:
    """
    Assign a user to a branch, transferring them out of their previous
    branch automatically (User.branch_id is a single FK — overwriting
    it IS the transfer).

    Raises:
      404 — target branch not found or belongs to a different org.
      404 — user not found or belongs to a different org.
      409 — user is already assigned to this branch.
    """
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

    user_result = await session.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == organization_id,
        )
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or does not belong to this organization.",
        )

    if user.branch_id == branch_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already assigned to this branch.",
        )

    old_branch_id = user.branch_id
    user.branch_id = branch_id
    await session.flush()
    await session.refresh(user)

    logger.info(
        "User transferred to branch",
        extra={
            "user_id": user_id,
            "old_branch_id": old_branch_id,
            "new_branch_id": branch_id,
            "org_id": organization_id,
        },
    )
    return user


async def assign_branch_manager(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    manager_id: str,
) -> Branch:
    """
    Assign a user as the manager of this branch.

    The manager does not need to be a home-branch member here —
    manager_id and User.branch_id are independent pointers. If the
    product requires the manager to also be a member, add that check
    and decide whether to auto-transfer or reject.

    On assignment, the manager is seeded with every branch-level
    permission (scope='both') scoped to this branch — identical to
    what invite-time branch_manager registration does.

    Raises:
      404 — branch not found or belongs to a different org.
      404 — manager_id not found or does not belong to this org.
      409 — branch already has a manager assigned (unassign first).
    """
    branch_result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    branch = branch_result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found or does not belong to this organization.",
        )

    if branch.manager_id is not None:
        if branch.manager_id == manager_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This user is already the manager of this branch.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Branch already has a manager assigned. Unassign the "
                "current manager before assigning a new one."
            ),
        )

    manager_result = await session.execute(
        select(User).where(
            User.id == manager_id,
            User.organization_id == organization_id,
        )
    )
    if manager_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or does not belong to this organization.",
        )

    branch.manager_id = manager_id
    await session.flush()

    await seed_role_permissions_for_branch(
        session,
        user_id=manager_id,
        organization_id=organization_id,
        branch_id=branch_id,
    )

    await session.refresh(branch)

    logger.info(
        "Branch manager assigned",
        extra={"branch_id": branch_id, "manager_id": manager_id, "org_id": organization_id},
    )
    return branch


async def unassign_branch_manager(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
) -> Branch:
    """
    Remove the current manager from this branch and revoke the
    branch-scoped permissions that were seeded on assignment.

    Only this branch's seeded grants are revoked. If the outgoing
    manager also manages another branch, those rows have a different
    branch_id and are left completely intact.

    Raises:
      404 — branch not found or belongs to a different org.
      409 — branch has no manager assigned.
    """
    branch_result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    branch = branch_result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found or does not belong to this organization.",
        )

    if branch.manager_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Branch has no manager assigned.",
        )

    old_manager_id = branch.manager_id
    branch.manager_id = None
    await session.flush()

    await unseed_role_permissions_for_branch(
        session,
        user_id=old_manager_id,
        organization_id=organization_id,
        branch_id=branch_id,
    )

    await session.refresh(branch)

    logger.info(
        "Branch manager unassigned",
        extra={"branch_id": branch_id, "old_manager_id": old_manager_id, "org_id": organization_id},
    )
    return branch