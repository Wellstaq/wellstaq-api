"""
app/services/branch/delete_branch.py

Branch deletion.

Rules:
  1. A branch with an assigned manager (branch.manager_id is not None)
     cannot be deleted — the manager must be explicitly unassigned first
     (via edit_branch with manager_id=null, or a dedicated unassign step).
     This is checked BEFORE the member-transfer logic, and independently
     of whether that manager also happens to be a branch member — see
     module note below on why these are separate checks.
  2. A branch with members (User.branch_id == this branch) cannot be
     deleted unless target_branch_id is provided — all members are
     transferred there first, in the same transaction.

Why manager and membership are checked separately:
  Branch.manager_id and User.branch_id are independent pointers — a
  manager is not required to also be a branch member (see Branch model
  docstring). Relying on the member-transfer sweep alone could silently
  leave manager_id pointing at a user who was never actually processed.
  Requiring explicit manager removal makes the precondition unambiguous:
  by the time deletion proceeds, manager_id is guaranteed to already be
  NULL, not "SET NULL as a side effect of the DELETE."

Note on scope: this only handles USER membership transfer, per the
explicit requirement. Other branch-scoped data (departments assigned
to this branch, clubs, branch-scoped Role, UserPermission grants) is
NOT transferred:
  - Department.branch_id is ondelete="SET NULL" and has no ORM cascade
    on Branch.departments — deleting a branch disconnects departments
    (branch_id → NULL) rather than deleting them. This is intentional:
    a department belongs to the org, not the branch: confirmed correct.
  - Branch.clubs has cascade="all, delete-orphan" AND ondelete="CASCADE"
    — deleting a branch DELETES its clubs. This is destructive and
    happens regardless of member/manager state. Flag if clubs need the
    same "must reassign before delete" treatment.
  - Branch.role (branch-scoped Role) also cascades on delete.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User

logger = logging.getLogger(__name__)


async def delete_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    target_branch_id: str | None = None,
) -> None:
    """
    Delete a branch.

    Raises:
      404 — branch not found or belongs to a different org.
      409 — branch still has an assigned manager (must be unassigned first).
      409 — branch has members and no target_branch_id was provided.
      400 — target_branch_id equals branch_id (can't transfer to self).
      404 — target_branch_id provided but not found / not in this org.
    """
    # ── Validate branch belongs to this org ─────────────────────────────────
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

    # ── Guard: manager must be unassigned first ─────────────────────────────
    # Checked independently of membership — manager_id may point at a user
    # whose own branch_id is elsewhere (see module docstring), so this
    # can't be folded into the member-transfer step below.
    if branch.manager_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Branch has an assigned manager. Unassign the manager "
                "(set manager_id to null via branch update) before deleting."
            ),
        )

    # ── Count members currently in this branch ──────────────────────────────
    count_result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(User.branch_id == branch_id, User.organization_id == organization_id)
    )
    member_count = count_result.scalar_one()

    if member_count > 0:
        if target_branch_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Branch has {member_count} member(s). Provide "
                    "target_branch_id to transfer them before deletion."
                ),
            )

        if target_branch_id == branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_branch_id cannot be the same as the branch being deleted.",
            )

        # ── Validate target branch belongs to this org ──────────────────────
        target_result = await session.execute(
            select(Branch).where(
                Branch.id == target_branch_id,
                Branch.organization_id == organization_id,
            )
        )
        if target_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target branch not found or does not belong to this organization.",
            )

        # ── Bulk transfer members ────────────────────────────────────────────
        await session.execute(
            update(User)
            .where(User.branch_id == branch_id, User.organization_id == organization_id)
            .values(branch_id=target_branch_id)
        )
        await session.flush()

        logger.info(
            "Branch members transferred ahead of deletion",
            extra={
                "from_branch_id": branch_id,
                "to_branch_id": target_branch_id,
                "member_count": member_count,
                "org_id": organization_id,
            },
        )

    await session.delete(branch)
    await session.flush()

    logger.info(
        "Branch deleted",
        extra={"branch_id": branch_id, "org_id": organization_id},
    )