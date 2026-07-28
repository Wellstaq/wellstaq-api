"""
app/services/payment/checkout_service.py
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.payment import PaymentTransaction, SubscriptionPlan
from app.services.payment._helpers import get_subscription_or_404
from app.services.payment.paystack_client import PaystackError, initialize_transaction

logger = logging.getLogger(__name__)


def _generate_reference() -> str:
    return f"wellstaq_{secrets.token_urlsafe(16)}"


async def initiate_checkout(
    *, session: AsyncSession, organization_id: str, plan_id: str, initiating_user: User,
    auto_renew: bool = False, callback_url: str | None = None,
) -> dict:
    """
    plan_id: the plan the org is checking out INTO — explicit, not
    inferred from whatever plan they're currently on. This matters most
    when converting from the free Trial plan to a real paid plan:
    without this, checkout would silently charge against Trial's
    amount=0, which is wrong. The subscription's plan_id is switched to
    this plan only once payment actually succeeds (see
    webhook_service.handle_charge_success), never here — this function
    only initiates the charge attempt.
    """
    subscription = await get_subscription_or_404(session=session, organization_id=organization_id)

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
        if not plan.external_plan_id:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "This plan is not linked to a Paystack plan — recurring billing cannot be initiated.",
            )
        plan_code = plan.external_plan_id

    reference = _generate_reference()

    transaction = PaymentTransaction(
        organization_id=organization_id, subscription_id=subscription.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status="pending", external_reference=reference,
    )
    session.add(transaction)
    await session.flush()

    try:
        paystack_data = await initialize_transaction(
            email=initiating_user.email, amount=plan.amount, currency=plan.currency,
            reference=reference, callback_url=callback_url, plan_code=plan_code,
        )
    except PaystackError as exc:
        logger.error("Paystack checkout initialization failed", extra={"organization_id": organization_id})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Payment initialization failed: {exc.message}")

    return {"authorization_url": paystack_data["authorization_url"], "reference": reference, "access_code": paystack_data.get("access_code")}