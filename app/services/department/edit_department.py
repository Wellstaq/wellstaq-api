"""
app/services/department/edit_department.py

Update operations for departments.

edit_department — patch name, description, avatar_initial on an existing department.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department

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
                    Department.id != department_id,          # exclude self
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