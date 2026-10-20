"""
app/services/organization/org_directory.py

Read-only "organization directory" queries — members, clubs, and
(for convenience) branches/departments scoped to an org.

Branches and departments already have first-class list endpoints
(GET /organizations/{org_id}/branches, GET /organizations/{org_id}/departments)
in their own routers
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, DepartmentMember, Organization, Role, User, UserRoleAssignment, UserSettings

logger = logging.getLogger(__name__)


async def _require_org(session: AsyncSession, organization_id: str) -> Organization:
    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    return org


async def get_organization_members(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """Members with the role belonging to the requested org/branch slot."""
    organization = await _require_org(session, organization_id)

    member_filters = [User.organization_id == organization_id]
    if branch_id is not None:
        # Team directories contain home-branch members only. Cross-branch role
        # holders are resolved explicitly from UserRoleAssignment queries.
        member_filters.append(User.branch_id == branch_id)
    count_result = await session.execute(
        select(func.count(func.distinct(User.id))).select_from(User).where(*member_filters)
    )
    total = count_result.scalar_one()

    query = select(User, DepartmentMember.department_id, Role.id.label("scoped_role_id"), Role.name.label("role_name"), UserSettings.public_profile).outerjoin(
        DepartmentMember, DepartmentMember.user_id == User.id,
    )
    if branch_id is None:
        query = query.outerjoin(Role, Role.id == User.role_id)
    else:
        query = query.outerjoin(UserRoleAssignment, (
            (UserRoleAssignment.user_id == User.id)
            & (UserRoleAssignment.organization_id == organization_id)
            & (UserRoleAssignment.branch_id == branch_id)
        )).outerjoin(Role, Role.id == UserRoleAssignment.role_id)
    result = await session.execute(
        query.outerjoin(UserSettings, UserSettings.user_id == User.id)
        .where(*member_filters)
        .order_by(User.first_name, User.last_name)
        .offset(offset)
        .limit(limit)
    )
    members = [
        {
            **user.__dict__,
            "email": "—" if user.email.endswith("@deleted.wellstaq.invalid") else user.email,
            "department_id": department_id,
            "role_id": scoped_role_id,
            "role_name": role_name,
            "public_profile": bool(public_profile),
            "is_owner": user.id == organization.owner_id,
        }
        for user, department_id, scoped_role_id, role_name, public_profile in result.all()
    ]
    if branch_id is not None:
        for member in members:
            member["organization_role_id"] = None
            member["organization_role_name"] = None
            member["branch_roles"] = ([{
                "branch_id": branch_id,
                "role_id": member["role_id"],
                "role_name": member["role_name"],
            }] if member["role_id"] and member["role_name"] else [])
    if branch_id is None and members:
        user_ids = [member["id"] for member in members]
        assignment_rows = (await session.execute(
            select(UserRoleAssignment.user_id, UserRoleAssignment.branch_id, Role.id, Role.name)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .where(
                UserRoleAssignment.organization_id == organization_id,
                UserRoleAssignment.user_id.in_(user_ids),
                UserRoleAssignment.branch_id.is_not(None),
            )
        )).all()
        branch_roles: dict[str, list[dict[str, str]]] = {}
        for user_id, assigned_branch_id, role_id, assigned_role_name in assignment_rows:
            branch_roles.setdefault(user_id, []).append({
                "branch_id": assigned_branch_id,
                "role_id": role_id,
                "role_name": assigned_role_name,
            })
        for member in members:
            member["organization_role_id"] = member["role_id"]
            member["organization_role_name"] = member["role_name"]
            member["branch_roles"] = branch_roles.get(member["id"], [])
    return members, total


async def get_organization_clubs(
    *,
    session: AsyncSession,
    organization_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Club], int]:
    """All clubs belonging to the organization, across every branch."""
    await _require_org(session, organization_id)

    count_result = await session.execute(
        select(func.count()).select_from(Club).where(Club.organization_id == organization_id)
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(Club)
        .where(Club.organization_id == organization_id)
        .order_by(Club.name)
        .offset(offset)
        .limit(limit)
    )
    clubs = result.scalars().all()
    return clubs, total
