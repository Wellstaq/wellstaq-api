"""
app/services/department/department_creation.py

Business logic for department operations.

Contract:
  - All functions accept a live AsyncSession.
  - None of them call commit() — the router owns that.
  - flush() is called after INSERT to populate the PK before the router
    serializes the response.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession


from app.models import Organization, Department

logger = logging.getLogger(__name__)


async def create_department(
    *,
    session: AsyncSession,
    organization_id: str,
    name: str,
    description: str | None,
    avatar_initial: str | None,
    created_by: str,
) -> Department:
    # Org must exist and be active
    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is deactivated.",
        )

    # Normalize name before uniqueness check and storage
    name = name.strip()  # remove accidental leading/trailing spaces too

    # Case-insensitive name uniqueness within the org
    duplicate = await session.execute(
        select(Department).where(
            Department.organization_id == organization_id,
            func.lower(Department.name) == name.lower(),
        )
    )
    if duplicate.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A department named '{name}' already exists in this organization.",
        )

    department = Department(
        name=name,          # stores the user's casing e.g. "Engineering"
        description=description,
        avatar_initial=avatar_initial,
        organization_id=organization_id,
        branch_id=None,
        created_by=created_by,
        member_count=0,
    )
    session.add(department)
    await session.flush()
    await session.refresh(department)

    logger.info(
        "Department created",
        extra={
            "department_id": department.id,
            "org_id": organization_id,
            "created_by": created_by,
        },
    )
    return department