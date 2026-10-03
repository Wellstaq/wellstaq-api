"""Paystack webhook reconciliation and subscription lifecycle handlers."""

from __future__ import annotations

import hashlib
import hmac
import logging
from calendar import monthrange
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import OrganizationSubscription, PaymentTransaction, SubscriptionPlan, User

logger = logging.getLogger(__name__)


def _parse_gateway_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Paystack returned an invalid datetime", extra={"value": value})
        return None


def _subscription_payload(event_data: dict) -> dict:
    nested = event_data.get("subscription")
    return nested if isinstance(nested, dict) else event_data


def _customer_payload(event_data: dict) -> dict:
    customer = event_data.get("customer")
    return customer if isinstance(customer, dict) else {}


def _metadata_payload(event_data: dict) -> dict:
    metadata = event_data.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _gateway_plan_code(event_data: dict) -> str | None:
    for candidate in (
        _subscription_payload(event_data).get("plan"),
        event_data.get("plan"),
    ):
        if isinstance(candidate, dict) and candidate.get("plan_code"):
            return str(candidate["plan_code"])
        if isinstance(candidate, str) and candidate.startswith("PLN_"):
            return candidate
    return None


def _next_period_end(start: datetime, plan: SubscriptionPlan) -> datetime:
    if plan.billing_interval == "annual":
        year = start.year + 1
        day = min(start.day, monthrange(year, start.month)[1])
        return start.replace(year=year, day=day)
    month_index = start.month
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def verify_paystack_signature(*, payload_body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    computed = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"), payload_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header)


async def _find_subscription_for_gateway_event(
    *, session: AsyncSession, event_data: dict
) -> OrganizationSubscription | None:
    subscription_data = _subscription_payload(event_data)
    subscription_code = subscription_data.get("subscription_code")
    if subscription_code:
        result = await session.execute(
            select(OrganizationSubscription).where(
                OrganizationSubscription.external_subscription_id == str(subscription_code)
            )
        )
        subscription = result.scalar_one_or_none()
        if subscription is not None:
            return subscription

    metadata = _metadata_payload(event_data)
    internal_id = metadata.get("subscription_id")
    if internal_id:
        result = await session.execute(
            select(OrganizationSubscription).where(OrganizationSubscription.id == str(internal_id))
        )
        subscription = result.scalar_one_or_none()
        if subscription is not None:
            return subscription

    customer = _customer_payload(event_data)
    customer_code = customer.get("customer_code")
    if customer_code:
        result = await session.execute(
            select(OrganizationSubscription).where(
                OrganizationSubscription.external_customer_id == str(customer_code)
            )
        )
        subscription = result.scalar_one_or_none()
        if subscription is not None:
            return subscription

    email = customer.get("email")
    if email:
        result = await session.execute(
            select(OrganizationSubscription)
            .join(User, User.organization_id == OrganizationSubscription.organization_id)
            .where(User.email == str(email).strip().lower())
        )
        return result.scalars().first()
    return None


async def _load_plan(session: AsyncSession, plan_id: str) -> SubscriptionPlan:
    result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The paid subscription plan no longer exists.")
    return plan


async def handle_charge_success(*, session: AsyncSession, event_data: dict) -> None:
    reference = event_data.get("reference")
    if not reference:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment payload missing reference.")
    reference = str(reference)

    result = await session.execute(
        select(PaymentTransaction).where(PaymentTransaction.external_reference == reference)
    )
    transaction = result.scalar_one_or_none()
    is_recurring_charge = transaction is None

    if transaction is not None and transaction.status == "succeeded":
        return

    subscription: OrganizationSubscription | None = None
    if transaction is None:
        subscription = await _find_subscription_for_gateway_event(
            session=session, event_data=event_data
        )
        if subscription is None:
            logger.warning(
                "Successful charge cannot be mapped to a subscription",
                extra={"reference": reference},
            )
            return
        plan = await _load_plan(session, subscription.plan_id)
        transaction = PaymentTransaction(
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            plan_id=plan.id,
            amount=plan.amount,
            currency=plan.currency,
            status="pending",
            external_reference=reference,
            is_recurring=True,
        )
        session.add(transaction)
    else:
        plan = await _load_plan(session, transaction.plan_id)
        result = await session.execute(
            select(OrganizationSubscription).where(
                OrganizationSubscription.id == transaction.subscription_id
            )
        )
        subscription = result.scalar_one_or_none()

    webhook_amount = event_data.get("amount")
    webhook_currency = event_data.get("currency")
    gateway_plan_code = _gateway_plan_code(event_data)
    if (
        gateway_plan_code
        and plan.external_plan_id
        and gateway_plan_code != plan.external_plan_id
    ):
        transaction.status = "failed"
        transaction.failure_reason = (
            f"Plan mismatch: expected {plan.external_plan_id}, provider reported {gateway_plan_code}."
        )
        await session.flush()
        return
    if webhook_amount != transaction.amount:
        transaction.status = "failed"
        transaction.failure_reason = (
            f"Amount mismatch: expected {transaction.amount}, provider reported {webhook_amount}."
        )
        await session.flush()
        return
    if not webhook_currency or str(webhook_currency).upper() != transaction.currency.upper():
        transaction.status = "failed"
        transaction.failure_reason = (
            f"Currency mismatch: expected {transaction.currency}, provider reported {webhook_currency}."
        )
        await session.flush()
        return

    transaction.status = "succeeded"
    authorization = event_data.get("authorization")
    authorization = authorization if isinstance(authorization, dict) else {}
    card_type = authorization.get("card_type")
    last4 = authorization.get("last4")
    if card_type and last4:
        transaction.payment_method = f"{card_type} ending in {last4}"
    elif event_data.get("channel"):
        transaction.payment_method = str(event_data["channel"])

    if subscription is not None:
        paid_at = _parse_gateway_datetime(event_data.get("paid_at")) or datetime.now(timezone.utc)
        subscription_data = _subscription_payload(event_data)
        next_payment_date = _parse_gateway_datetime(
            subscription_data.get("next_payment_date") or event_data.get("next_payment_date")
        )
        period_start = _parse_gateway_datetime(event_data.get("period_start")) or paid_at

        subscription.status = "active"
        subscription.current_period_start = period_start
        subscription.current_period_end = next_payment_date or _next_period_end(period_start, plan)
        subscription.plan_id = transaction.plan_id
        subscription.cancelled_at = None

        subscription_code = subscription_data.get("subscription_code")
        if subscription_code:
            subscription.external_subscription_id = str(subscription_code)
            subscription.email_token = subscription_data.get("email_token") or subscription.email_token
            subscription.auto_renew = True
        elif not is_recurring_charge and not transaction.is_recurring:
            # A known checkout without subscription context is a one-time
            # payment. subscription.create will restore these fields shortly
            # afterward when this checkout did include a Paystack plan.
            subscription.external_subscription_id = None
            subscription.email_token = None
            subscription.auto_renew = False

        customer_code = _customer_payload(event_data).get("customer_code")
        if customer_code:
            subscription.external_customer_id = str(customer_code)

    await session.flush()
    logger.info(
        "Payment succeeded, subscription activated",
        extra={"organization_id": transaction.organization_id, "reference": reference},
    )


async def handle_charge_failed(*, session: AsyncSession, event_data: dict) -> None:
    reference = event_data.get("reference")
    if not reference:
        return
    result = await session.execute(
        select(PaymentTransaction).where(PaymentTransaction.external_reference == str(reference))
    )
    transaction = result.scalar_one_or_none()
    if transaction is None or transaction.status != "pending":
        return
    transaction.status = "failed"
    transaction.failure_reason = str(event_data.get("gateway_response") or "Payment failed.")
    await session.flush()


async def handle_subscription_create(*, session: AsyncSession, event_data: dict) -> None:
    subscription = await _find_subscription_for_gateway_event(session=session, event_data=event_data)
    if subscription is None:
        logger.warning("subscription.create could not be mapped to an organization")
        return

    subscription_data = _subscription_payload(event_data)
    subscription_code = subscription_data.get("subscription_code")
    if not subscription_code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Subscription payload missing subscription_code.")

    subscription.external_subscription_id = str(subscription_code)
    subscription.email_token = subscription_data.get("email_token") or subscription.email_token
    subscription.auto_renew = True
    customer_code = _customer_payload(event_data).get("customer_code")
    if customer_code:
        subscription.external_customer_id = str(customer_code)
    next_payment_date = _parse_gateway_datetime(subscription_data.get("next_payment_date"))
    if next_payment_date:
        subscription.current_period_end = next_payment_date
    await session.flush()


async def handle_invoice_payment_failed(*, session: AsyncSession, event_data: dict) -> None:
    subscription = await _find_subscription_for_gateway_event(session=session, event_data=event_data)
    if subscription is None:
        logger.warning("invoice.payment_failed for unknown subscription")
        return
    subscription.status = "past_due"
    await session.flush()


async def handle_subscription_not_renewing(*, session: AsyncSession, event_data: dict) -> None:
    subscription = await _find_subscription_for_gateway_event(session=session, event_data=event_data)
    if subscription is None:
        return
    subscription.auto_renew = False
    await session.flush()


async def handle_subscription_disable(*, session: AsyncSession, event_data: dict) -> None:
    subscription = await _find_subscription_for_gateway_event(session=session, event_data=event_data)
    if subscription is None:
        return
    if subscription.status != "cancelled":
        subscription.cancelled_at = datetime.now(timezone.utc)
    subscription.status = "cancelled"
    subscription.auto_renew = False
    await session.flush()
