"""
app/services/payment/trial_service.py

start_trial — provisions a 7-day free trial subscription. Called once,
automatically, at org creation — never a manual/user-triggered action.
No payment or gateway interaction happens at this step; the org simply
gets `status="trialing"` with no external_customer_id yet. That gets
populated later, once actual payment collection is wired to a real
gateway (see TODO(payment-gateway) throughout this module).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import OrganizationSubscription, SubscriptionPlan

logger = logging.getLogger(__name__)

TRIAL_LENGTH = timedelta(days=7)


async def start_trial(*, session: AsyncSession, organization_id: str, plan_id: str | None = None) -> OrganizationSubscription:
    """
    plan_id: defaults to the single active plan if omitted — once
    multiple plans exist, callers should pass the plan the org actually
    signed up for. For now, with exactly one plan seeded, omitting it
    is the common case.
    """
    existing = await session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.organization_id == organization_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization already has a subscription.")

    if plan_id is None:
        plan_result = await session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True)).limit(1)
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "No active subscription plan configured.")
        plan_id = plan.id

    now = datetime.now(timezone.utc)
    trial_ends_at = now + TRIAL_LENGTH

    subscription = OrganizationSubscription(
        organization_id=organization_id,
        plan_id=plan_id,
        status="trialing",
        current_period_start=now,
        current_period_end=trial_ends_at,  # trial period IS the current billing window until payment starts
        trial_ends_at=trial_ends_at,
    )
    session.add(subscription)
    await session.flush()
    await session.refresh(subscription)

    logger.info("Trial started", extra={"organization_id": organization_id, "trial_ends_at": str(trial_ends_at)})
    return subscription