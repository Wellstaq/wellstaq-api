"""
app/services/branch/delete_branch.py

Branch deletion.

Rules (checked in this order):
  1. Headquarters branch cannot be deleted — ever. The org must be
     deactivated or deleted as a whole instead.
  2. A branch with an assigned manager cannot be deleted — the manager
     must be explicitly unassigned first.
  3. A branch with departments cannot be deleted — they must be
     explicitly deleted first (no department-transfer operation exists;
     branch_id is immutable on Department after creation, so the only
     path is deleting the department(s) via delete_department).
  4. A branch with home-branch members cannot be deleted unless BOTH
     target_branch_id AND target_department_id are provided — all such
     users are transferred to the target branch AND enrolled in the
     target department, in the same transaction as the delete.

Why target_department_id is required, not optional:
  The app's product rule is that a user should never be without a
  department. Guard 3 already guarantees every department under THIS
  branch is gone by the time Guard 4 runs — which means every
  home-branch member here has already lost their DepartmentMember row
  (via Department's own cascade, triggered by whatever earlier call
  deleted the department). If Guard 4 only moved User.branch_id and
  stopped there, those users would land in the target branch with no
  department and no path back to one (there is no standalone "remove
  member from department" endpoint, so nothing else in the app would
  ever fix this after the fact). Requiring target_department_id closes
  that gap in the same transaction instead of leaving it as a manual
  follow-up someone has to remember.

Why manager and membership are checked separately:
  Branch.manager_id and User.branch_id are independent pointers — a
  manager is not required to also be a branch member. Requiring explicit
  manager removal makes the precondition unambiguous: by the time
  deletion proceeds, manager_id is already NULL, not "SET NULL as a
  side effect of the DELETE."

Schema note — Department.branch_id is NOT NULL + CASCADE:
  Deleting a branch with departments still attached would CASCADE-delete
  every department and its members. Rule 3 blocks this explicitly rather
  than letting it happen silently. Do not remove that guard without
  confirming the product wants cascade-delete behaviour here.

What cascades automatically on branch delete (no code needed):
  - Clubs (cascade="all, delete-orphan" on Branch.clubs)
  - Custom Roles scoped to this branch (Role.branch_id ondelete=CASCADE)
  - UserRoleAssignment rows scoped to this branch (oversight positions,
    e.g. who holds branch_manager here) — these are NOT the same as
    User.branch_id (home). Rule 4 above handles home; role assignment
    rows simply vanish via their FK cascade.
  - Branch-scoped UserPermission grants (UserPermission.branch_id FK)
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User
from app.models.department import Department, DepartmentMember

logger = logging.getLogger(__name__)


async def delete_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    target_branch_id: str | None = None,
    target_department_id: str | None = None,
) -> None:
    """
    Delete a branch.

    target_branch_id / target_department_id are only required if the
    branch has home-branch members (User.branch_id == branch_id). Both
    must be provided together — a user can't be moved to a new branch
    without also landing in a department there, and target_department_id
    must belong to target_branch_id specifically, not just to the org.

    Raises:
      404 — branch not found or belongs to a different org.
      403 — branch is the Headquarters branch (undeletable).
      409 — branch still has an assigned manager.
      409 — branch still has departments.
      409 — branch has home-branch members and target_branch_id and/or
            target_department_id were not given.
      409 — one or more members being transferred already belong to a
            department outside this branch (see defensive check below).
      400 — target_branch_id equals branch_id.
      404 — target_branch_id not found / not in this org.
      404 — target_department_id not found, not in this org, or does
            not belong to target_branch_id.
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
                "To shut down the organization entirely, use the org deactivation flow."
            ),
        )

    # ── Guard 2: manager must be unassigned first ──────────────────────────────
    if branch.manager_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Branch has an assigned manager. Unassign the manager "
                "before deleting."
            ),
        )

    # ── Guard 3: departments must be cleared first ─────────────────────────────
    dept_count_result = await session.execute(
        select(func.count())
        .select_from(Department)
        .where(Department.branch_id == branch_id, Department.organization_id == organization_id)
    )
    department_count = dept_count_result.scalar_one()
    if department_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch has {department_count} department(s). Delete them first — "
                "departments cannot be moved to another branch."
            ),
        )

    # ── Guard 4: home-branch members must be transferred (branch + dept) first ─
    count_result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(User.branch_id == branch_id, User.organization_id == organization_id)
    )
    member_count = count_result.scalar_one()

    if member_count > 0:
        if target_branch_id is None or target_department_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Branch has {member_count} member(s). Provide both "
                    "target_branch_id and target_department_id so they can "
                    "be transferred — a member can't be moved to a new "
                    "branch without also being placed in a department there."
                ),
            )

        if target_branch_id == branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_branch_id cannot be the same as the branch being deleted.",
            )

        target_branch_result = await session.execute(
            select(Branch).where(
                Branch.id == target_branch_id,
                Branch.organization_id == organization_id,
            )
        )
        if target_branch_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target branch not found or does not belong to this organization.",
            )

        # target_department_id must belong to target_branch_id specifically
        # — not just to the org — otherwise a transferred member's home
        # branch and department would disagree, the exact mismatch this
        # whole guard exists to prevent.
        target_dept_result = await session.execute(
            select(Department).where(
                Department.id == target_department_id,
                Department.organization_id == organization_id,
                Department.branch_id == target_branch_id,
            )
        )
        if target_dept_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Target department not found, does not belong to this "
                    "organization, or does not belong to the target branch."
                ),
            )

        transferring_ids_result = await session.execute(
            select(User.id).where(
                User.branch_id == branch_id,
                User.organization_id == organization_id,
            )
        )
        transferring_user_ids = [row[0] for row in transferring_ids_result.all()]

        # Defensive check: none of these users should already hold a
        # DepartmentMember row (Guard 3 already required every department
        # under THIS branch to be gone, and branch/department alignment is
        # enforced at invite time — see send_invite Guard 6 — so a
        # home-branch member of this branch shouldn't have a department
        # elsewhere). Checked explicitly rather than trusted blindly,
        # since assign_user_to_branch() can move a user's home branch
        # independently of their department, which could leave a stale
        # DepartmentMember row pointing somewhere unexpected. If this
        # fires, inserting a new DepartmentMember row below would violate
        # DepartmentMember's one-department-per-user UniqueConstraint —
        # better to surface that clearly here than as a raw IntegrityError.
        existing_membership_result = await session.execute(
            select(DepartmentMember.user_id).where(
                DepartmentMember.user_id.in_(transferring_user_ids)
            )
        )
        already_has_department = {row[0] for row in existing_membership_result.all()}
        if already_has_department:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{len(already_has_department)} member(s) being transferred "
                    "already belong to a department outside this branch. "
                    "Resolve their department membership before deleting this branch."
                ),
            )

        # Transfer home branch. UserRoleAssignment rows (oversight) are a
        # separate FK and cascade away with the branch — no update needed.
        await session.execute(
            update(User)
            .where(User.branch_id == branch_id, User.organization_id == organization_id)
            .values(branch_id=target_branch_id)
        )

        # Enroll every transferred user in the target department, in the
        # same transaction — see module docstring for why this can't be
        # a separate follow-up step.
        session.add_all([
            DepartmentMember(department_id=target_department_id, user_id=user_id)
            for user_id in transferring_user_ids
        ])

        await session.flush()

        logger.info(
            "Branch members transferred (branch + department) ahead of deletion",
            extra={
                "from_branch_id": branch_id,
                "to_branch_id": target_branch_id,
                "to_department_id": target_department_id,
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