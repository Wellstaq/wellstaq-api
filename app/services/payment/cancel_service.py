"""
app/services/payment/cancel_service.py

Cancels auto-renewal for an org's subscription — turns off future
auto-charges via Paystack, but does NOT immediately revoke access. The
org keeps 'active' status through current_period_end (they already paid
for this period), then the expire_subscriptions job naturally flips
them to past_due once that period ends, same as a non-auto-renew org
that simply didn't renew.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment._helpers import get_subscription_or_404
from app.services.payment.paystack_client import PaystackError, disable_subscription, enable_subscription

logger = logging.getLogger(__name__)


async def cancel_auto_renew(*, session: AsyncSession, organization_id: str):
    subscription = await get_subscription_or_404(session=session, organization_id=organization_id)

    if not subscription.auto_renew:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This organization is not on auto-renew.")

    if not subscription.external_subscription_id or not subscription.email_token:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Missing Paystack subscription reference — cannot cancel via API. Contact support.",
        )

    try:
        await disable_subscription(
            subscription_code=subscription.external_subscription_id, email_token=subscription.email_token,
        )
    except PaystackError as exc:
        logger.error("Failed to disable Paystack subscription", extra={"organization_id": organization_id})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not cancel auto-renew: {exc.message}")

    subscription.auto_renew = False
    await session.flush()
    logger.info("Auto-renew cancelled", extra={"organization_id": organization_id})
    return subscription


async def set_auto_renew(*, session: AsyncSession, organization_id: str, enabled: bool):
    subscription = await get_subscription_or_404(session=session, organization_id=organization_id)
    if subscription.auto_renew == enabled:
        return subscription
    if not enabled:
        return await cancel_auto_renew(session=session, organization_id=organization_id)
    if not subscription.external_subscription_id or not subscription.email_token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Automatic renewal can only be enabled after completing a recurring-plan checkout.",
        )
    try:
        await enable_subscription(
            subscription_code=subscription.external_subscription_id,
            email_token=subscription.email_token,
        )
    except PaystackError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not enable auto-renew: {exc.message}")
    subscription.auto_renew = True
    await session.flush()
    return subscription
