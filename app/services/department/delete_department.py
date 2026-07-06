"""
app/services/department/delete_department.py

Delete a department, transferring members atomically if needed.

Business rules:
  - If the department has members, deletion requires one of two things
    from the caller:
      1. Provide transfer_to_department_id, so all members are moved
         to that department atomically as part of the delete, or
      2. Reassign every member to another department beforehand
         (outside this call), so member_count is already 0 by the
         time delete is invoked.
    If neither has happened, raise 409. A member without a department
    is an invalid state the app cannot tolerate (downstream code
    assumes every member has one), so we never allow it implicitly.
  - If transfer_to_department_id is provided, move all DepartmentMember
    rows to the target department in the same transaction, adjusting
    member_count on both departments.
  - If the department is the head's only department link, head_user_id
    is cleared automatically via the ondelete="SET NULL" FK — no
    extra app-layer step needed.
  - Delete is hard — no soft delete.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department, DepartmentMember
from app.services.errors import AppError


async def delete_department(
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    transfer_to_department_id: str | None,
) -> dict:
    # Load the department
    result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = result.scalar_one_or_none()
    if not department:
        raise AppError("Department not found.", status.HTTP_404_NOT_FOUND)

    # Load current members
    members_result = await session.execute(
        select(DepartmentMember).where(
            DepartmentMember.department_id == department_id,
        )
    )
    members = list(members_result.scalars().all())
    member_count = len(members)

    if member_count > 0 and not transfer_to_department_id:
        raise AppError(
            f"Department has {member_count} member(s). Either provide "
            "transfer_to_department_id to move them to another department "
            "as part of this delete, or reassign them to another "
            "department first and retry.",
            status.HTTP_409_CONFLICT,
        )

    transferred = 0
    if transfer_to_department_id and members:
        # Validate target department
        target_result = await session.execute(
            select(Department).where(
                Department.id == transfer_to_department_id,
                Department.organization_id == organization_id,
            )
        )
        target = target_result.scalar_one_or_none()
        if not target:
            raise AppError(
                "Transfer target department not found in this organization.",
                status.HTTP_404_NOT_FOUND,
            )
        if target.id == department_id:
            raise AppError(
                "Cannot transfer members to the department being deleted.",
                status.HTTP_400_BAD_REQUEST,
            )

        # Move all members to target department atomically
        for member in members:
            member.department_id = transfer_to_department_id

        target.member_count += member_count
        transferred = member_count

    await session.flush()
    await session.delete(department)
    await session.flush()

    return {
        "department_id": department_id,
        "organization_id": organization_id,
        "members_transferred": transferred,
    }