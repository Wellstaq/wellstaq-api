"""
app/services/payment/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.constants import ACTIVE_STATUSES

from app.models.payment import OrganizationSubscription



async def get_subscription_or_404(*, session: AsyncSession, organization_id: str) -> OrganizationSubscription:
    result = await session.execute(
        select(OrganizationSubscription)
        .options(selectinload(OrganizationSubscription.plan))
        .where(OrganizationSubscription.organization_id == organization_id)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This organization has no subscription record.")
    return subscription