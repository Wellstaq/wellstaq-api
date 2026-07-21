"""
scripts/seed_categories.py

Global lookup seeder for Category (department/club badges) and
HealthCategory (health assessment categories — currently dormant per
the owner's decision to strip health-condition tracking from the UI for
now, but the table and seed stay in place for when it returns).

Run once during initial setup, and again whenever new categories are
added. Existing rows are never touched — only missing ones are
inserted. Mirrors seed_permissions.py / seed_wellbeing.py conventions.

Usage:
    python -m scripts.seed_categories
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.category import Category, HealthCategory

logger = logging.getLogger(__name__)


# ── Category catalogue (department/club badges) ─────────────────────────────
# badge_color values are placeholders — swap for your actual design
# system's tokens/hex codes.

CATEGORIES: list[dict] = [
    {"name": "Engineering", "badge_color": "#4F46E5"},
    {"name": "Sales", "badge_color": "#F59E0B"},
    {"name": "Marketing", "badge_color": "#EC4899"},
    {"name": "Human Resources", "badge_color": "#10B981"},
    {"name": "Finance", "badge_color": "#0EA5E9"},
    {"name": "Operations", "badge_color": "#8B5CF6"},
    {"name": "Customer Support", "badge_color": "#F43F5E"},
    {"name": "Wellness & Fitness", "badge_color": "#22C55E"},
]

# ── HealthCategory catalogue — dormant per owner's decision, seeded but
# not currently surfaced in any active route/feature. ───────────────────

HEALTH_CATEGORIES: list[str] = [
    "Heart Disease",
    "Kidney Disease",
    "High Cholesterol",
    "Iron Deficiency",
    "Diabetes",
]


async def seed_categories(session: AsyncSession) -> None:
    inserted = 0
    skipped = 0
    for entry in CATEGORIES:
        result = await session.execute(select(Category).where(Category.name == entry["name"]))
        if result.scalar_one_or_none():
            skipped += 1
            continue
        session.add(Category(name=entry["name"], badge_color=entry["badge_color"]))
        inserted += 1

    await session.flush()
    logger.info("Category seed complete — inserted: %d, skipped: %d", inserted, skipped)
    print(f"✓ Categories: inserted {inserted}, skipped {skipped} existing.")


async def seed_health_categories(session: AsyncSession) -> None:
    inserted = 0
    skipped = 0
    for name in HEALTH_CATEGORIES:
        result = await session.execute(select(HealthCategory).where(HealthCategory.name == name))
        if result.scalar_one_or_none():
            skipped += 1
            continue
        session.add(HealthCategory(name=name))
        inserted += 1

    await session.flush()
    logger.info("Health category seed complete — inserted: %d, skipped: %d", inserted, skipped)
    print(f"✓ Health categories: inserted {inserted}, skipped {skipped} existing.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async for session in get_db():
        await seed_categories(session)
        await seed_health_categories(session)
        await session.commit()
        break


if __name__ == "__main__":
    asyncio.run(main())