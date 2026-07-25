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