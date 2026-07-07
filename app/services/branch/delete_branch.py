"""
app/services/branch/delete_branch.py

Branch deletion.

Rules (checked in this order):
  1. Headquarters branch cannot be deleted — ever.
  2. A branch with an assigned manager cannot be deleted — unassign first.
  3. A branch with departments cannot be deleted — delete them first via
     delete_department(), which handles member transfers atomically.
  4. A branch with home-branch members cannot be deleted — this should
     never be reachable in practice if Rule 3 was followed (deleting a
     department transfers its members to another branch/department), but
     it is checked explicitly as a safety net for edge cases where
     User.branch_id was moved independently of department membership.

Deletion order expected from the caller:
  For each department in the branch:
    → call delete_department(department_id, transfer_to_department_id=<other>)
       which atomically moves all DepartmentMembers to the target department
       in another branch (since this branch's departments are being cleared)
  Once all departments are gone, all members have already left.
  → call delete_branch(branch_id)

What cascades automatically on branch delete (no code needed):
  - Clubs          (Branch.clubs cascade="all, delete-orphan")
  - Custom Roles   (Role.branch_id ondelete=CASCADE)
  - UserRoleAssignment rows scoped to this branch (oversight positions)
  - Branch-scoped UserPermission grants (UserPermission.branch_id FK)
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User
from app.models.department import Department

logger = logging.getLogger(__name__)


async def delete_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
) -> None:
    """
    Delete a branch. The branch must be fully empty before deletion is allowed.

    Raises:
      404 — branch not found or belongs to a different org.
      403 — branch is the Headquarters branch (undeletable).
      409 — branch still has an assigned manager.
      409 — branch still has departments (delete them via delete_department first).
      409 — branch still has home-branch members (safety net — should not be
             reachable if departments were cleared via delete_department, which
             transfers members as part of its own transaction).
    """
    # ── Validate branch belongs to this org ───────────────────────────────────
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

    # ── Guard 1: HQ is permanent ───────────────────────────────────────────────
    if branch.is_headquarters:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The Headquarters branch cannot be deleted. "
                "To shut down the organization, use the org deactivation flow."
            ),
        )

    # ── Guard 2: manager must be unassigned first ──────────────────────────────
    if branch.manager_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Branch has an assigned manager. Unassign the manager before deleting.",
        )

    # ── Guard 3: departments must be deleted first ─────────────────────────────
    dept_count_result = await session.execute(
        select(func.count())
        .select_from(Department)
        .where(
            Department.branch_id == branch_id,
            Department.organization_id == organization_id,
        )
    )
    department_count = dept_count_result.scalar_one()
    if department_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch has {department_count} department(s). "
                "Delete each department first — use delete_department() with "
                "transfer_to_department_id to move their members atomically. "
                "Once all departments are cleared, retry this deletion."
            ),
        )

    # ── Guard 4: no home-branch members (safety net) ───────────────────────────
    # Under normal flow this is always 0 after Guard 3 passes — delete_department
    # transfers members to another branch as part of its own transaction.
    # This guard catches the edge case where User.branch_id was reassigned
    # independently of department membership (e.g. via assign_user_to_branch),
    # leaving a user whose home is this branch but whose department is elsewhere.
    member_count_result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(
            User.branch_id == branch_id,
            User.organization_id == organization_id,
        )
    )
    member_count = member_count_result.scalar_one()
    if member_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch still has {member_count} member(s) assigned to it as their "
                "home branch. Reassign them to another branch before deleting. "
                "(This should not occur in normal usage — it may indicate a member "
                "whose branch was moved independently of their department.)"
            ),
        )

    # ── Delete ─────────────────────────────────────────────────────────────────
    await session.delete(branch)
    await session.flush()

    logger.info(
        "Branch deleted",
        extra={"branch_id": branch_id, "org_id": organization_id},
    )