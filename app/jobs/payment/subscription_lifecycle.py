"""
app/jobs/payment/subscription_lifecycle.py

Two automatic transitions, both handled here since they're the same
underlying pattern (a deadline passed with no payment):

  trialing -> past_due  when trial_ends_at has passed with no payment.
  active   -> past_due  when current_period_end has passed with no
                         renewal. Necessary because this system uses
                         manual renewal (the customer re-initiates
                         checkout each period via initiate_checkout) —
                         there is no auto-recurring billing wired up via
                         Paystack's Subscriptions API. Without this
                         check, a customer who paid once would show as
                         'active' forever even after their period
                         genuinely ended.

active -> active (successful renewal) only ever happens via the
charge.success webhook (see webhook_service.py) — never by this job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.database import get_db
from app.models.payment import OrganizationSubscription

logger = logging.getLogger(__name__)


async def expire_subscriptions(ctx: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    trial_expired = 0
    period_expired = 0

    async for session in get_db():
        trial_result = await session.execute(
            select(OrganizationSubscription).where(
                OrganizationSubscription.status == "trialing",
                OrganizationSubscription.trial_ends_at.is_not(None),
                OrganizationSubscription.trial_ends_at <= now,
            )
        )
        for subscription in trial_result.scalars().all():
            subscription.status = "past_due"
            trial_expired += 1

        period_result = await session.execute(
            select(OrganizationSubscription).where(
                OrganizationSubscription.status == "active",
                OrganizationSubscription.current_period_end <= now,
            )
        )
        for subscription in period_result.scalars().all():
            subscription.status = "past_due"
            period_expired += 1

        await session.commit()
        break

    logger.info(
        "Subscriptions expired",
        extra={"trial_expired": trial_expired, "period_expired": period_expired},
    )