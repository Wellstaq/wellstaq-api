"""
app/services/payment/get_subscription.py
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import OrganizationSubscription, PaymentTransaction
from app.services.payment._helpers import get_subscription_or_404


async def get_subscription(*, session: AsyncSession, organization_id: str) -> OrganizationSubscription:
    return await get_subscription_or_404(session=session, organization_id=organization_id)


async def get_transaction_history(*, session: AsyncSession, organization_id: str, offset: int = 0, limit: int = 50):
    from sqlalchemy import func

    query = (
        select(PaymentTransaction).where(PaymentTransaction.organization_id == organization_id)
        .order_by(PaymentTransaction.created_at.desc()).offset(offset).limit(limit)
    )
    count_query = select(func.count()).select_from(PaymentTransaction).where(PaymentTransaction.organization_id == organization_id)

    total = (await session.execute(count_query)).scalar_one()
    transactions = (await session.execute(query)).scalars().all()
    return list(transactions), total


async def get_available_plans(*, session: AsyncSession, organization_id: str | None = None):
    from app.models.payment import OrganizationSubscription, SubscriptionPlan

    query = select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True))
    if organization_id is not None:
        trial_ends_at = (
            await session.execute(
                select(OrganizationSubscription.trial_ends_at).where(
                    OrganizationSubscription.organization_id == organization_id
                )
            )
        ).scalar_one_or_none()
        if trial_ends_at is not None:
            query = query.where(SubscriptionPlan.name != "Trial")

    result = await session.execute(query.order_by(SubscriptionPlan.amount.asc()))
    return list(result.scalars().all())
