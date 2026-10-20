"""
app/services/payment/checkout_service.py
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.payment import PaymentTransaction, SubscriptionPlan
from app.services.payment._helpers import get_subscription_or_404
from app.services.payment.paystack_client import (
    PaystackError,
    create_plan,
    fetch_plan,
    initialize_transaction,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

TRUSTED_PAYMENT_CALLBACK_ORIGINS = {
    "https://appwellstaq.vercel.app",
    "https://appwellstaq6.vercel.app",
    "https://app.wellstaq.com",
}


def _generate_reference() -> str:
    return f"wellstaq_{secrets.token_urlsafe(16)}"


def _validate_callback_url(callback_url: str | None) -> str | None:
    if callback_url is None:
        return None
    parsed = urlparse(callback_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed_origins = {
        value.rstrip("/")
        for value in [
            *settings.PAYMENT_CALLBACK_ALLOWED_ORIGINS,
            *settings.CORS_ORIGINS,
            settings.PUBLIC_APP_ORIGIN,
        ]
        if value
    } | TRUSTED_PAYMENT_CALLBACK_ORIGINS
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or origin not in allowed_origins:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment callback URL is not an approved application origin.")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment callback URL must use HTTPS.")
    return callback_url


async def _ensure_paystack_plan(*, session: AsyncSession, plan: SubscriptionPlan) -> str:
    """Return a plan code belonging to the currently configured Paystack account."""
    if plan.external_plan_id:
        try:
            await fetch_plan(plan.external_plan_id)
            return plan.external_plan_id
        except PaystackError as exc:
            # A key/account change leaves the old account's plan codes in our
            # database. Only recreate on a genuine not-found response; auth or
            # network failures must remain visible instead of creating plans.
            if exc.status_code not in {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND}:
                raise

    interval = "annually" if plan.billing_interval == "annual" else "monthly"
    created = await create_plan(
        name=plan.name,
        amount=plan.amount,
        interval=interval,
        currency=plan.currency,
    )
    plan_code = created.get("plan_code")
    if not isinstance(plan_code, str) or not plan_code:
        raise PaystackError("Paystack did not return a plan code.")
    plan.external_plan_id = plan_code
    await session.flush()
    return plan_code

async def initiate_checkout(
    *, session: AsyncSession, organization_id: str, plan_id: str, initiating_user: User,
    auto_renew: bool = False, callback_url: str | None = None,
) -> dict:
    subscription = await get_subscription_or_404(session=session, organization_id=organization_id)
    callback_url = _validate_callback_url(callback_url)
    now = datetime.now(timezone.utc)
    period_end = subscription.current_period_end
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)

    if subscription.status == "active" and period_end > now:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This organization already has an active subscription. Wait until the current billing period ends before starting another checkout.",
        )

    cutoff = now - timedelta(minutes=settings.PAYMENT_PENDING_EXPIRY_MINUTES)
    stale_result = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.organization_id == organization_id,
            PaymentTransaction.status == "pending",
            PaymentTransaction.created_at <= cutoff,
        )
    )
    for stale_transaction in stale_result.scalars().all():
        stale_transaction.status = "cancelled"
        stale_transaction.failure_reason = "Checkout expired before a payment confirmation was received."
    await session.flush()

    # ── Idempotency guard ────────────────────────────────────────────────────
    # If there's already a PENDING transaction for this org, reject a
    # new checkout attempt rather than creating a second parallel one —
    # prevents double-submission (e.g. a user double-clicking "pay") from
    # generating duplicate pending transactions (even across plans).
    existing_pending = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.organization_id == organization_id,
            PaymentTransaction.status == "pending",
        )
    )
    if existing_pending.scalars().first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A payment is already in progress for this organization. Complete it or wait for it to expire before retrying.",
        )

    plan_result = await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id, SubscriptionPlan.is_active.is_(True))
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found or is not active.")

    if plan.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot check out into a free plan.")

    plan_code = None
    if auto_renew:
        try:
            plan_code = await _ensure_paystack_plan(session=session, plan=plan)
        except PaystackError as exc:
            logger.error("Paystack recurring plan preparation failed", extra={"plan_id": plan.id})
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Could not prepare automatic renewal: {exc.message}",
            ) from exc

    reference = _generate_reference()

    transaction = PaymentTransaction(
        organization_id=organization_id, subscription_id=subscription.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status="pending", external_reference=reference,
        is_recurring=auto_renew,
    )
    session.add(transaction)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A payment is already in progress for this organization.",
        ) from exc

    try:
        paystack_data = await initialize_transaction(
            email=initiating_user.email, amount=plan.amount, currency=plan.currency,
            reference=reference, callback_url=callback_url, plan_code=plan_code,
            metadata={
                "organization_id": organization_id,
                "subscription_id": subscription.id,
                "plan_id": plan.id,
                "auto_renew": auto_renew,
            },
        )
    except PaystackError as exc:
        logger.error("Paystack checkout initialization failed", extra={"organization_id": organization_id})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Payment initialization failed: {exc.message}")

    return {"authorization_url": paystack_data["authorization_url"], "reference": reference, "access_code": paystack_data.get("access_code")}
