"""
app/services/wellbeing/lookup_service.py

Read-only global lookups — WellbeingDimension/WellbeingChallenge are
shared across every org, seeded once (see scripts/seed_wellbeing.py),
never created via the API.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing import WellbeingChallenge, WellbeingDimension


async def list_dimensions(*, session: AsyncSession) -> list[WellbeingDimension]:
    result = await session.execute(select(WellbeingDimension).order_by(WellbeingDimension.name.asc()))
    return list(result.scalars().all())


async def list_wellbeing_challenges(*, session: AsyncSession) -> list[WellbeingChallenge]:
    result = await session.execute(select(WellbeingChallenge).order_by(WellbeingChallenge.name.asc()))
    return list(result.scalars().all())