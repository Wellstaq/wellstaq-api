"""
app/services/payment/plan_limit.py

Tier-based usage limit enforcement. Called from inside domain services
(create_department, create_club, etc.) at the moment of creation — NOT
a route-level dependency, since the check is action-specific (current
count vs. this plan's limit for that specific resource), not a blanket
"is this org allowed to do anything" gate. See require_active_subscription
in app/auth/dependencies.py for that separate, blanket check.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payment._helpers import get_subscription_or_404


async def enforce_plan_limit(
    *, session: AsyncSession, organization_id: str, limit_key: str, current_count: int
) -> None:
    """
    Checks current_count against the org's active plan limit for
    limit_key (e.g. 'max_departments'). A missing key or a None value
    in plan.features both mean unlimited — no check applied. Raises 402
    if current_count has already reached the limit.
    """
    subscription = await get_subscription_or_404(session=session, organization_id=organization_id)
    plan = subscription.plan

    limit = plan.features.get(limit_key)
    if limit is None:
        return

    if current_count >= limit:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your current plan allows up to {limit} for this resource. Upgrade to add more.",
        )