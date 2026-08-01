"""
scripts/seed_wellbeing.py

Global wellbeing lookup seeder — WellbeingDimension and WellbeingChallenge
are shared across every organization (not org-scoped), same category as
Permission/Role. Run once during initial setup, and again whenever new
dimensions/challenge categories are added.

Usage:
    python -m scripts.seed_wellbeing

Transaction contract: mirrors seed_permissions.py — seed_dimensions() and
seed_wellbeing_challenges() only flush, never commit. main() owns commit.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.shared import get_job_session
from app.models.wellbeing import WellbeingChallenge, WellbeingDimension

logger = logging.getLogger(__name__)


DIMENSIONS: list[str] = [
    "Physical",
    "Mental",
    "Financial",
    "Social",
    "Career",
]

WELLBEING_CHALLENGES: list[str] = [
    "Fitness",
    "Nutrition",
    "Mental Health",
    "Sleep",
    "Mindfulness",
    "Financial Wellness",
]


async def seed_dimensions(session: AsyncSession) -> None:
    inserted = 0
    skipped = 0
    for name in DIMENSIONS:
        result = await session.execute(select(WellbeingDimension).where(WellbeingDimension.name == name))
        if result.scalar_one_or_none():
            skipped += 1
            continue
        session.add(WellbeingDimension(name=name))
        inserted += 1

    await session.flush()
    logger.info("Dimension seed complete — inserted: %d, skipped: %d", inserted, skipped)
    print(f"✓ Wellbeing dimensions: inserted {inserted}, skipped {skipped} existing.")


async def seed_wellbeing_challenges(session: AsyncSession) -> None:
    inserted = 0
    skipped = 0
    for name in WELLBEING_CHALLENGES:
        result = await session.execute(select(WellbeingChallenge).where(WellbeingChallenge.name == name))
        if result.scalar_one_or_none():
            skipped += 1
            continue
        session.add(WellbeingChallenge(name=name))
        inserted += 1

    await session.flush()
    logger.info("Wellbeing challenge seed complete — inserted: %d, skipped: %d", inserted, skipped)
    print(f"✓ Wellbeing challenges: inserted {inserted}, skipped {skipped} existing.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with get_job_session() as session:
        await seed_dimensions(session)
        await seed_wellbeing_challenges(session)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())