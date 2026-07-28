"""
scripts/seed_paystack_plans.py

One-time setup script: creates a Paystack Plan for every SubscriptionPlan
row that doesn't yet have an external_plan_id, and writes the resulting
Paystack plan_code back onto that row. Safe to re-run — skips any plan
that already has external_plan_id set.

Run once per environment (test keys vs live keys create separate plans
on Paystack's side, since test/live are fully isolated).

Usage:
    python -m scripts.create_paystack_plans
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.payment import SubscriptionPlan

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"

# Paystack's billing_interval vocabulary differs slightly from yours —
# map "monthly"/"annual" (your CheckConstraint) to Paystack's expected
# "monthly"/"annually".
INTERVAL_MAP = {"monthly": "monthly", "annual": "annually"}


async def create_paystack_plan(*, plan: SubscriptionPlan) -> str:
    payload = {
        "name": plan.name,
        "amount": plan.amount,
        "interval": INTERVAL_MAP[plan.billing_interval],
        "currency": plan.currency,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{PAYSTACK_BASE_URL}/plan",
            json=payload,
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"},
        )

    body = response.json()
    if not body.get("status"):
        raise RuntimeError(f"Paystack plan creation failed for '{plan.name}': {body.get('message')}")

    return body["data"]["plan_code"]


async def create_missing_plans(session: AsyncSession) -> None:
    result = await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.external_plan_id.is_(None), SubscriptionPlan.amount > 0)
    )
    plans = result.scalars().all()
    

    if not plans:
        print("✓ Every plan already has an external_plan_id — nothing to do.")
        return

    for plan in plans:
        plan_code = await create_paystack_plan(plan=plan)
        plan.external_plan_id = plan_code
        print(f"✓ Created Paystack plan for '{plan.name}' -> {plan_code}")

    await session.flush()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async for session in get_db():
        await create_missing_plans(session)
        await session.commit()
        break


if __name__ == "__main__":
    asyncio.run(main())