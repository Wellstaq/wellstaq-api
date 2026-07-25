"""
app/services/payment/checkout_service.py
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.payment import PaymentTransaction
from app.services.payment._helpers import get_subscription_or_404
from app.services.payment.paystack_client import PaystackError, initialize_transaction

logger = logging.getLogger(__name__)


def _generate_reference() -> str:
    return f"wellstaq_{secrets.token_urlsafe(16)}"


async def initiate_checkout(
    *, session: AsyncSession, organization_id: str, initiating_user: User, callback_url: str | None = None
) -> dict:
    subscription = await get_subscription_or_404(session=session, organization_id=organization_id)
    plan = subscription.plan

    if not plan.external_plan_id:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "This plan is not linked to a Paystack plan — recurring billing cannot be initiated.",
        )

    reference = _generate_reference()

    transaction = PaymentTransaction(
        organization_id=organization_id, subscription_id=subscription.id,
        amount=plan.amount, currency=plan.currency, status="pending", external_reference=reference,
    )
    session.add(transaction)
    await session.flush()

    try:
        paystack_data = await initialize_transaction(
            email=initiating_user.email, amount=plan.amount, currency=plan.currency,
            reference=reference, callback_url=callback_url,
            plan_code=plan.external_plan_id,  # new — this is what turns it into a subscription
        )
    except PaystackError as exc:
        logger.error("Paystack checkout initialization failed", extra={"organization_id": organization_id})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Payment initialization failed: {exc.message}")

    return {"authorization_url": paystack_data["authorization_url"], "reference": reference, "access_code": paystack_data.get("access_code")}