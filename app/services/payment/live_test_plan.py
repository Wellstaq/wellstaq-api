"""Temporary live-payment plan requested for end-to-end billing checks."""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.payment import SubscriptionPlan

PLAN_ID = "09f00d23-5724-490f-a790-b57011b34e50"


async def ensure_live_test_plan(session):
    # A stable key makes startup safe with multiple web workers. Existing rows
    # are never reactivated, so disabling this plan later stays effective.
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    await session.execute(insert(SubscriptionPlan).values(
        id=PLAN_ID,
        name="Live Payment Test (Temporary)",
        billing_interval="monthly",
        amount=10000,
        currency="NGN",
        is_active=True,
        features={"max_employees": 2, "max_departments": 2, "max_clubs": 2,
                  "max_branches": 2, "max_events": 2, "max_challenges": 2},
    ).on_conflict_do_nothing(index_elements=["id"]))
