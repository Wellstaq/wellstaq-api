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

# app/services/payment/trial_service.py

async def start_trial(*, session: AsyncSession, organization_id: str) -> OrganizationSubscription:
    existing = await session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.organization_id == organization_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization already has a subscription.")

    plan_result = await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "Trial", SubscriptionPlan.is_active.is_(True))
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "No active Trial plan configured.")

    now = datetime.now(timezone.utc)
    trial_ends_at = now + TRIAL_LENGTH

    subscription = OrganizationSubscription(
        organization_id=organization_id, plan_id=plan.id, status="trialing",
        current_period_start=now, current_period_end=trial_ends_at, trial_ends_at=trial_ends_at,
    )
    session.add(subscription)
    await session.flush()
    await session.refresh(subscription)
    return subscription