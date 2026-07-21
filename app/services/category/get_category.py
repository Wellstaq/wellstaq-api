"""
app/services/category/get_category.py

Read-only — Category and HealthCategory are seed data, never created
via the API, same pattern as Hashtag/WellbeingDimension.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category, HealthCategory


async def list_categories(*, session: AsyncSession) -> list[Category]:
    result = await session.execute(select(Category).order_by(Category.name.asc()))
    return list(result.scalars().all())


async def list_health_categories(*, session: AsyncSession) -> list[HealthCategory]:
    result = await session.execute(select(HealthCategory).order_by(HealthCategory.name.asc()))
    return list(result.scalars().all())