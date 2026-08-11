"""
app/services/kpi_snapshot/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch


async def validate_branch(*, session: AsyncSession, organization_id: str, branch_id: str) -> None:
    result = await session.execute(
        select(Branch).where(Branch.id == branch_id, Branch.organization_id == organization_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found or does not belong to this organization.")