"""
app/services/payment/webhook_service.py

Handles Paystack's charge.success webhook — the ACTUAL source of truth
for whether a payment succeeded (never trust a frontend redirect alone;
a user can close the browser after paying but before redirect, or fake
a redirect without paying — the webhook, signed by Paystack is trusted).

Signature verification: Paystack signs every webhook body with your
secret key via HMAC-SHA512, sent in the x-paystack-signature header.
Verifying this is NOT optional — without it, anyone could POST a fake
'payment succeeded' body to your webhook endpoint and get free access.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import OrganizationSubscription, PaymentTransaction, SubscriptionPlan

logger = logging.getLogger(__name__)


def verify_paystack_signature(*, payload_body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    computed = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"), payload_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header)


async def handle_charge_success(*, session: AsyncSession, event_data: dict) -> None:
    reference = event_data.get("reference")
    if not reference:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook payload missing reference.")

    result = await session.execute(
        select(PaymentTransaction).where(PaymentTransaction.external_reference == reference)
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        logger.warning("Webhook for unknown transaction reference", extra={"reference": reference})
        return

    if transaction.status == "succeeded":
        return

    transaction.status = "succeeded"

    authorization = event_data.get("authorization", {})
    card_type = authorization.get("card_type")
    last4 = authorization.get("last4")
    if card_type and last4:
        transaction.payment_method = f"{card_type} ending in {last4}"
    elif event_data.get("channel"):
        transaction.payment_method = event_data.get("channel")

    subscription_result = await session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.id == transaction.subscription_id)
    )
    subscription = subscription_result.scalar_one_or_none()

    if subscription is not None:
        now = datetime.now(timezone.utc)

        # ── THIS is where the block you asked about goes ──────────────
        plan_result = await session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == transaction.plan_id)
        )
        paid_plan = plan_result.scalar_one_or_none()
        billing_period = timedelta(days=30) if paid_plan.billing_interval == "monthly" else timedelta(days=365)

        subscription.status = "active"
        subscription.current_period_start = now
        subscription.current_period_end = now + billing_period
        subscription.plan_id = transaction.plan_id
        # ────────────────────────────────────────────────────────────

        if subscription.external_subscription_id is None:
            subscription_data = event_data.get("subscription", {})
            subscription_code = subscription_data.get("subscription_code")
            if subscription_code:
                subscription.external_subscription_id = subscription_code
                subscription.email_token = subscription_data.get("email_token")
                subscription.auto_renew = True

    await session.flush()
    logger.info("Payment succeeded, subscription activated", extra={"organization_id": transaction.organization_id})


async def handle_charge_failed(*, session: AsyncSession, event_data: dict) -> None:
    reference = event_data.get("reference")
    if not reference:
        return

    result = await session.execute(select(PaymentTransaction).where(PaymentTransaction.external_reference == reference))
    transaction = result.scalar_one_or_none()
    if transaction is None or transaction.status != "pending":
        return

    transaction.status = "failed"
    transaction.failure_reason = event_data.get("gateway_response", "Payment failed.")
    await session.flush()
    logger.info("Payment failed", extra={"reference": reference})

async def handle_invoice_payment_failed(*, session: AsyncSession, event_data: dict) -> None:
    """
    A recurring auto-charge failed (card declined, insufficient funds,
    etc.) — Paystack tells us via this event rather than us discovering
    it later. Immediately flips the subscription to past_due, rather
    than waiting for the nightly expire_lapsed_subscriptions job to
    eventually notice current_period_end has passed.
    """
    subscription_code = event_data.get("subscription", {}).get("subscription_code")
    if not subscription_code:
        return

    result = await session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.external_subscription_id == subscription_code)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        logger.warning("invoice.payment_failed for unknown subscription", extra={"subscription_code": subscription_code})
        return

    subscription.status = "past_due"
    await session.flush()
    logger.info("Subscription marked past_due after failed renewal", extra={"organization_id": subscription.organization_id})


async def handle_subscription_disable(*, session: AsyncSession, event_data: dict) -> None:
    """Paystack cancelled/disabled the subscription (e.g. too many
    failed retries, or explicit cancellation)."""
    subscription_code = event_data.get("subscription_code")
    if not subscription_code:
        return

    result = await session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.external_subscription_id == subscription_code)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        return

    from datetime import datetime, timezone
    subscription.status = "cancelled"
    subscription.cancelled_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info("Subscription cancelled via webhook", extra={"organization_id": subscription.organization_id})