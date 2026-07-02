"""
app/services/branch/update_branch.py

Update operations for branches.

  edit_branch            — patch name, manager_id
  assign_user_to_branch  — assign/transfer a user into a branch

Branch membership is a single FK (User.branch_id) rather than a join
table — a user can only ever have ONE branch_id value at the DB level,
so "a user must not belong to multiple branches" is enforced by the
column itself. assign_user_to_branch's job is: validate everything,
then overwrite that FK, transferring the user out of whatever branch
they were previously in (if any) implicitly.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User

logger = logging.getLogger(__name__)

async def edit_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str | None,
) -> Branch:
    """
    Partially update a branch's editable fields.

    manager_id is intentionally NOT handled here — it has its own
    dedicated endpoints (assign_branch_manager / unassign_branch_manager)
    so there is exactly one path that mutates it, with its own validation
    (manager must belong to the org, can't silently overwrite an existing
    manager, etc). Keeping it out of this generic PATCH avoids two code
    paths disagreeing on those rules.

    Raises:
      404 — branch not found or belongs to a different org.
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
    Assign a user to a branch, transferring them out of any previous
    branch automatically (User.branch_id is a single nullable FK, so a
    user can never be in two branches at once — overwriting the column
    IS the transfer).

    Raises:
      404 — target branch not found or belongs to a different org.
      404 — user not found or belongs to a different org.
      409 — user is already assigned to this branch.
    """
    # ── Validate target branch belongs to this org ──────────────────────────
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

    # ── Validate user exists and belongs to this org ────────────────────────
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

    # ── Already in this branch — nothing to do ───────────────────────────────
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

    Note: this does NOT require the manager to also be a member of this
    branch (User.branch_id) — manager_id and branch_id are independent
    pointers, see Branch model docstring. If your p oduct rule requires
    the manager to also be a branch member, add that check here (and
    decide whether to auto-transfer them or reject the assignment).

    Raises:
      404 — branch not found or belongs to a different org.
      404 — manager_id not found or does not belong to this org.
      409 — branch already has a different manager assigned (must be
            unassigned first — no implicit replace).
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
    Remove the current manager from this branch (manager_id -> None).

    Idempotent-ish: raises 409 if there's no manager to remove, rather
    than silently succeeding — callers should know whether their action
    actually did anything, e.g. before retrying a branch delete.

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
    await session.refresh(branch)

    logger.info(
        "Branch manager unassigned",
        extra={"branch_id": branch_id, "old_manager_id": old_manager_id, "org_id": organization_id},
    )
    return branch