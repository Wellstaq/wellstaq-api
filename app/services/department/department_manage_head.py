"""
app/services/department/manage_head.py

Assign or remove a department head.

When assigning:
  - Validates the user belongs to this department (a non-member
    cannot lead a department).
  - Sets department.head_user_id.
  - Assigns the team_leader system role to the user.
  - Updates DepartmentMember.role to "lead" for this user.

When removing:
  - Clears department.head_user_id.
  - Reverts DepartmentMember.role back to "member".
  - Role reversion (team_leader → member) is optional and controlled
    by a parameter — some orgs may want the user to keep the
    team_leader role even after stepping down.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department, DepartmentMember
from app.models import User, Role
from app.services.errors import AppError


async def assign_department_head(
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    user_id: str,
) -> Department:
    # Load department
    dept_result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = dept_result.scalar_one_or_none()
    if not department:
        raise AppError("Department not found.", status.HTTP_404_NOT_FOUND)

    # Validate the user is a member of this department
    member_result = await session.execute(
        select(DepartmentMember).where(
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise AppError(
            "User is not a member of this department. "
            "Assign them to the department before making them head.",
            status.HTTP_409_CONFLICT,
        )

    # Load user
    user_result = await session.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == organization_id,
        )
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise AppError("User not found in this organization.", status.HTTP_404_NOT_FOUND)

    # Assign team_leader system role to the user
    role_result = await session.execute(
        select(Role).where(
            Role.name == "team_leader",
            Role.is_system == True,  # noqa: E712
        )
    )
    team_leader_role = role_result.scalar_one_or_none()
    if not team_leader_role:
        raise AppError(
            "System role 'team_leader' not found — run the seed script.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Set head and update roles
    department.head_user_id = user_id
    member.role = "lead"
    user.role_id = team_leader_role.id
    await session.flush()

    return department


async def remove_department_head(
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    revert_role: bool = True,
) -> Department:
    # Load department
    dept_result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = dept_result.scalar_one_or_none()
    if not department:
        raise AppError("Department not found.", status.HTTP_404_NOT_FOUND)

    if not department.head_user_id:
        raise AppError(
            "This department has no head assigned.",
            status.HTTP_409_CONFLICT,
        )

    former_head_id = department.head_user_id

    # Clear department membership role
    member_result = await session.execute(
        select(DepartmentMember).where(
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == former_head_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if member:
        member.role = "member"

    # Optionally revert user's platform role back to member
    if revert_role:
        user_result = await session.execute(
            select(User).where(User.id == former_head_id)
        )
        user = user_result.scalar_one_or_none()
        if user:
            member_role_result = await session.execute(
                select(Role).where(
                    Role.name == "member",
                    Role.is_system == True,  # noqa: E712
                )
            )
            member_role = member_role_result.scalar_one_or_none()
            if member_role:
                user.role_id = member_role.id

    department.head_user_id = None
    await session.flush()

    return department