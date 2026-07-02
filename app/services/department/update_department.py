"""
app/services/department/update_department.py

Update operations for departments.

  edit_department          — patch name, description, avatar_initial
  assign_user_to_department — move a user into a department, removing
                              them from their current one if they have one
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, User
from app.models.department import DepartmentMember

logger = logging.getLogger(__name__)


async def edit_department(
    *,
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    name: str | None,
    description: str | None,
    avatar_initial: str | None,
) -> Department:
    """
    Partially update a department's editable fields.

    Only fields explicitly provided (not None) are updated, so callers
    can PATCH a single field without touching the others.

    Raises:
      404 — department not found or belongs to a different org.
      409 — new name conflicts with an existing department (case-insensitive).
    """
    result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = result.scalar_one_or_none()
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )

    if name is not None:
        name = name.strip()
        # Case-insensitive uniqueness check — skip if name is unchanged
        if name.lower() != department.name.lower():
            duplicate = await session.execute(
                select(Department).where(
                    Department.organization_id == organization_id,
                    Department.id != department_id,
                    func.lower(Department.name) == name.lower(),
                )
            )
            if duplicate.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A department named '{name}' already exists in this organization.",
                )
        department.name = name

    if description is not None:
        department.description = description

    if avatar_initial is not None:
        department.avatar_initial = avatar_initial

    await session.flush()
    await session.refresh(department)

    logger.info(
        "Department updated",
        extra={
            "department_id": department_id,
            "org_id": organization_id,
        },
    )
    return department


async def assign_user_to_department(
    *,
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    user_id: str,
) -> DepartmentMember:
    """
    Assign a user to a department within the organization.

    If the user already belongs to a department in this org, they are
    removed from it first before being placed in the new one. Both the
    removal and insertion happen in the same flush — the user is never
    left without a department mid-transaction.

    A user can only belong to ONE department at a time. This is enforced
    at both the service layer (this function) and the DB layer
    (UniqueConstraint("user_id") on DepartmentMember).

    Raises:
      404 — target department not found or belongs to a different org.
      404 — user not found or belongs to a different org.
      409 — user is already a member of the target department.
    """
    # ── Validate target department belongs to this org ─────────────────────────
    dept_result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = dept_result.scalar_one_or_none()
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found or does not belong to this organization.",
        )

    # ── Validate user exists and belongs to this org ───────────────────────────
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

    # ── Check for existing membership ──────────────────────────────────────────
    existing_result = await session.execute(
        select(DepartmentMember).where(
            DepartmentMember.user_id == user_id,
        )
    )
    existing_membership = existing_result.scalar_one_or_none()

    if existing_membership is not None:
        # Guard: already in the target department — nothing to do
        if existing_membership.department_id == department_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this department.",
            )

        # Remove from current department first.
        # flush() releases the UniqueConstraint("user_id") lock so the
        # subsequent INSERT does not collide with the old row.
        old_dept_id = existing_membership.department_id
        await session.delete(existing_membership)
        await session.flush()

        logger.info(
            "User removed from previous department",
            extra={
                "user_id": user_id,
                "old_department_id": old_dept_id,
                "org_id": organization_id,
            },
        )

    # ── Insert new membership ──────────────────────────────────────────────────
    new_membership = DepartmentMember(
        department_id=department_id,
        user_id=user_id,
    )
    session.add(new_membership)
    await session.flush()
    await session.refresh(new_membership)

    logger.info(
        "User assigned to department",
        extra={
            "user_id": user_id,
            "department_id": department_id,
            "org_id": organization_id,
        },
    )

    return new_membership
