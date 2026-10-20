import pytest
from sqlalchemy import select

from app.models.payment import SubscriptionPlan
from app.services.payment.live_test_plan import PLAN_ID, ensure_live_test_plan


@pytest.mark.asyncio
async def test_test_plan_is_one_hundred_naira_and_disabled_plan_stays_disabled(db_session):
    await ensure_live_test_plan(db_session)
    plan = (await db_session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == PLAN_ID))).scalar_one()
    assert (plan.amount, plan.currency, plan.billing_interval) == (10000, "NGN", "monthly")
    plan.is_active = False
    await db_session.flush()
    await ensure_live_test_plan(db_session)
    await db_session.refresh(plan)
    assert plan.is_active is False
