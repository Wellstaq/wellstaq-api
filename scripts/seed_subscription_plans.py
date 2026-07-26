"""
scripts/seed_subscription_plans.py

Seeds the one subscription plan Wellstaq currently offers. Multiple
plans are the future direction (see model docstring), but this seed
starts with exactly one — same "seed data, not user-created" pattern as
permissions/wellbeing dimensions.
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
from app.models.payment import SubscriptionPlan

logger = logging.getLogger(__name__)

PLANS: list[dict] = [
    {
        "name": "Standard",
        "billing_interval": "monthly",
        "amount": 500000,
        "currency": "NGN",
        "is_active": True,
        "features": {
            "max_employees": None,
            "max_departments": None,
            "max_clubs": None,
            "max_branches": None,
            "max_events": None,
            "max_challenges": None,
        },
    },
]


async def seed_plans(session: AsyncSession) -> None:
    inserted = 0
    skipped = 0
    for entry in PLANS:
        result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == entry["name"]))
        if result.scalar_one_or_none():
            skipped += 1
            continue
        session.add(SubscriptionPlan(**entry))
        inserted += 1

    await session.flush()
    logger.info("Subscription plan seed complete — inserted: %d, skipped: %d", inserted, skipped)
    print(f"✓ Subscription plans: inserted {inserted}, skipped {skipped} existing.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async for session in get_db():
        await seed_plans(session)
        await session.commit()
        break


if __name__ == "__main__":
    asyncio.run(main())