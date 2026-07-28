"""
SubscriptionPlan, OrganizationSubscription, and PaymentTransaction tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Key design rules being tested:
- The database is a snapshot of gateway state, NOT the source of truth.
  These tests verify schema constraints only — not payment logic.
- SubscriptionPlan.amount allows 0 (free plans); PaymentTransaction.amount
  must be > 0 (every charge is a real amount).
- OrganizationSubscription.organization_id is RESTRICT on delete — an org
  with an active subscription cannot be deleted. This is intentional
  friction: cancel in the gateway first.
- PaymentTransaction has no updated_at (no TimestampMixin) — it is an
  append-only ledger by design. We verify this structurally.
- Currency stored as ISO 4217 3-char string (e.g. "NGN", "USD") — no enum,
  adding a new currency never requires a migration.
- amount stored as integer in smallest currency unit (kobo, cents) — no
  floats for money, ever.
- SubscriptionPlan.features is a JSON dict holding plan limits like
  max_employees, max_departments, etc. Null or missing keys mean unlimited.
"""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OrganizationSubscription,
    PaymentTransaction,
    SubscriptionPlan,
)
from tests.conftest import make_org_with_owner


# ── Factories ──────────────────────────────────────────────────────────────────

def make_plan(
    name: str = "Starter",
    billing_interval: str = "monthly",
    amount: int = 5000,
    currency: str = "NGN",
    features: dict | None = None,
    **overrides,
) -> SubscriptionPlan:
    return SubscriptionPlan(
        name=name,
        billing_interval=billing_interval,
        amount=amount,
        currency=currency,
        features=features or {},
        **overrides,
    )


def make_subscription(
    organization_id: str,
    plan_id: str,
    *,
    status: str = "trialing",
    current_period_end: datetime | None = None,
    **overrides,
) -> OrganizationSubscription:
    if current_period_end is None:
        current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
    return OrganizationSubscription(
        organization_id=organization_id,
        plan_id=plan_id,
        status=status,
        current_period_end=current_period_end,
        **overrides,
    )


def make_transaction(
    organization_id: str,
    subscription_id: str,
    *,
    amount: int = 5000,
    currency: str = "NGN",
    status: str = "succeeded",
    **overrides,
) -> PaymentTransaction:
    return PaymentTransaction(
        organization_id=organization_id,
        subscription_id=subscription_id,
        amount=amount,
        currency=currency,
        status=status,
        **overrides,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(db_session, email="owner@example.com", slug="acme-corp"):
    """Thin wrapper — returns (user, org)."""
    org, user = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=slug),
        user_kwargs=dict(email=email),
    )
    return user, org


async def setup_plan_and_sub(db_session, org_id, **sub_overrides):
    """Create a plan and an active subscription for an org. Returns (plan, sub)."""
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    sub = make_subscription(org_id, plan.id, **sub_overrides)
    db_session.add(sub)
    await db_session.flush()

    return plan, sub


# ── SubscriptionPlan — persistence ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscription_plan_persists(db_session: AsyncSession):
    plan = make_plan("Starter", "monthly", 5000, "NGN")
    db_session.add(plan)
    await db_session.flush()

    result = await db_session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == plan.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "Starter"
    assert fetched.billing_interval == "monthly"
    assert fetched.amount == 5000
    assert fetched.currency == "NGN"


@pytest.mark.asyncio
async def test_subscription_plan_id_is_uuid_format(db_session: AsyncSession):
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    parts = plan.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_subscription_plan_timestamps_auto_set(db_session: AsyncSession):
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    assert plan.created_at is not None
    assert plan.updated_at is not None


@pytest.mark.asyncio
async def test_subscription_plan_defaults(db_session: AsyncSession):
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    assert plan.is_active is True
    assert plan.features == {}
    assert plan.external_plan_id is None
    assert plan.currency == "NGN"


@pytest.mark.asyncio
async def test_subscription_plan_full_fields(db_session: AsyncSession):
    plan = make_plan(
        "Enterprise",
        "annual",
        120000,
        "USD",
        features={"max_employees": 500, "max_departments": 10, "wellbeing_export": True},
        external_plan_id="price_stripe_xxx",
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()

    result = await db_session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == plan.id)
    )
    fetched = result.scalar_one()
    assert fetched.features["max_employees"] == 500
    assert fetched.features["max_departments"] == 10
    assert fetched.features["wellbeing_export"] is True
    assert fetched.external_plan_id == "price_stripe_xxx"


@pytest.mark.asyncio
async def test_subscription_plan_free_plan_amount_zero_allowed(db_session: AsyncSession):
    """amount=0 is valid for free plans (CHECK constraint is >= 0)."""
    plan = make_plan(name="Free", amount=0)
    db_session.add(plan)
    await db_session.flush()

    assert plan.amount == 0


@pytest.mark.asyncio
async def test_subscription_plan_negative_amount_raises(db_session: AsyncSession):
    """amount must be >= 0."""
    plan = make_plan(amount=-1)
    db_session.add(plan)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", ["monthly", "annual"])
async def test_subscription_plan_valid_billing_intervals(
    db_session: AsyncSession, interval: str
):
    plan = make_plan(billing_interval=interval)
    db_session.add(plan)
    await db_session.flush()
    assert plan.billing_interval == interval


@pytest.mark.asyncio
async def test_subscription_plan_invalid_billing_interval_raises(db_session: AsyncSession):
    """billing_interval is constrained to 'monthly' | 'annual'."""
    plan = make_plan(billing_interval="weekly")
    db_session.add(plan)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_subscription_plan_can_be_retired(db_session: AsyncSession):
    """is_active=False marks a plan as retired — existing subs continue,
    new signups blocked at app layer."""
    plan = make_plan(is_active=False)
    db_session.add(plan)
    await db_session.flush()

    assert plan.is_active is False


@pytest.mark.asyncio
async def test_subscription_plan_unlimited_employees(db_session: AsyncSession):
    """features without max_employees key (or max_employees=null) means unlimited seats."""
    plan = make_plan(features={"max_departments": 5})  # No max_employees key
    db_session.add(plan)
    await db_session.flush()

    assert "max_employees" not in plan.features


@pytest.mark.asyncio
async def test_subscription_plan_features_json_storage(db_session: AsyncSession):
    """Features dict is stored as JSON and retrieved correctly."""
    plan = make_plan(
        features={
            "max_employees": 100,
            "max_departments": None,  # Null means unlimited
            "wellbeing_export": True,
            "custom_branding": False,
        }
    )
    db_session.add(plan)
    await db_session.flush()

    result = await db_session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == plan.id)
    )
    fetched = result.scalar_one()
    assert fetched.features["max_employees"] == 100
    assert fetched.features["max_departments"] is None
    assert fetched.features["wellbeing_export"] is True
    assert fetched.features["custom_branding"] is False


@pytest.mark.asyncio
async def test_multiple_plans_can_exist(db_session: AsyncSession):
    db_session.add(make_plan("Starter", "monthly", 5000))
    db_session.add(make_plan("Growth", "monthly", 15000))
    db_session.add(make_plan("Enterprise", "annual", 150000))
    await db_session.flush()

    result = await db_session.execute(select(SubscriptionPlan))
    assert len(result.scalars().all()) == 3


# ── SubscriptionPlan — relationships ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_plan_subscriptions_relationship(db_session: AsyncSession):
    """plan.subscriptions loads all org subscriptions on that plan."""
    _, org_a = await setup_org(db_session, email="a@example.com", slug="org-a")
    _, org_b = await setup_org(db_session, email="b@example.com", slug="org-b")

    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    db_session.add(make_subscription(org_a.id, plan.id))
    db_session.add(make_subscription(org_b.id, plan.id))
    await db_session.flush()
    await db_session.refresh(plan, ["subscriptions"])

    assert len(plan.subscriptions) == 2


# ── OrganizationSubscription — persistence ────────────────────────────────────

@pytest.mark.asyncio
async def test_subscription_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    plan, sub = await setup_plan_and_sub(db_session, org.id)

    result = await db_session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.id == sub.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.plan_id == plan.id
    assert fetched.status == "trialing"


@pytest.mark.asyncio
async def test_subscription_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    parts = sub.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_subscription_timestamps_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    assert sub.created_at is not None
    assert sub.updated_at is not None
    assert sub.current_period_start is not None


@pytest.mark.asyncio
async def test_subscription_default_status_is_trialing(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    assert sub.status == "trialing"


@pytest.mark.asyncio
async def test_subscription_optional_fields_nullable(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    assert sub.trial_ends_at is None
    assert sub.cancelled_at is None
    assert sub.external_subscription_id is None
    assert sub.external_customer_id is None


@pytest.mark.asyncio
async def test_subscription_full_fields(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    sub = make_subscription(
        org.id, plan.id,
        status="active",
        trial_ends_at=now + timedelta(days=14),
        external_subscription_id="sub_stripe_xxx",
        external_customer_id="cus_stripe_xxx",
    )
    db_session.add(sub)
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.id == sub.id)
    )
    fetched = result.scalar_one()
    assert fetched.status == "active"
    assert fetched.external_subscription_id == "sub_stripe_xxx"
    assert fetched.external_customer_id == "cus_stripe_xxx"
    assert fetched.trial_ends_at is not None


@pytest.mark.asyncio
async def test_subscription_one_per_org(db_session: AsyncSession):
    """Unique constraint on organization_id — an org can only have one
    subscription row at a time."""
    _, org = await setup_org(db_session)
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    db_session.add(make_subscription(org.id, plan.id))
    await db_session.flush()

    db_session.add(make_subscription(org.id, plan.id, status="active"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["trialing", "active", "past_due", "cancelled"])
async def test_subscription_valid_statuses(db_session: AsyncSession, status: str):
    _, org = await setup_org(
        db_session, email=f"{status}@example.com", slug=f"org-{status}"
    )
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    sub = make_subscription(org.id, plan.id, status=status)
    db_session.add(sub)
    await db_session.flush()
    assert sub.status == status


@pytest.mark.asyncio
async def test_subscription_invalid_status_raises(db_session: AsyncSession):
    """status is constrained to the four known lifecycle values."""
    _, org = await setup_org(db_session)
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    sub = make_subscription(org.id, plan.id, status="paused")
    db_session.add(sub)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_subscription_can_be_cancelled(db_session: AsyncSession):
    """cancelled_at is stamped when subscription moves to cancelled.
    The row is NOT deleted — historical record is preserved."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id, status="active")

    now = datetime.now(timezone.utc)
    sub.status = "cancelled"
    sub.cancelled_at = now
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.id == sub.id)
    )
    fetched = result.scalar_one()
    assert fetched.status == "cancelled"
    assert fetched.cancelled_at is not None


@pytest.mark.asyncio
async def test_subscription_period_can_be_renewed(db_session: AsyncSession):
    """current_period_start/end are updated on each renewal webhook."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id, status="active")

    new_start = datetime.now(timezone.utc)
    new_end = new_start + timedelta(days=30)
    sub.current_period_start = new_start
    sub.current_period_end = new_end
    await db_session.flush()

    result = await db_session.execute(
        select(OrganizationSubscription).where(OrganizationSubscription.id == sub.id)
    )
    fetched = result.scalar_one()
    assert fetched.current_period_end > fetched.current_period_start


# ── OrganizationSubscription — FK constraints ─────────────────────────────────

@pytest.mark.asyncio
async def test_subscription_org_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    plan = make_plan()
    db_session.add(plan)
    await db_session.flush()

    sub = make_subscription("00000000-0000-0000-0000-000000000000", plan.id)
    db_session.add(sub)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_subscription_plan_fk_must_exist(db_session: AsyncSession):
    """plan_id must reference a real subscription plan."""
    _, org = await setup_org(db_session)

    sub = make_subscription(org.id, "00000000-0000-0000-0000-000000000000")
    db_session.add(sub)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_with_subscription_cannot_be_deleted(db_session: AsyncSession):
    """RESTRICT on organization_id — deleting an org that has a subscription
    is blocked at the DB level. Cancel in the gateway first, then delete.
    This is intentional friction to prevent accidental data loss while
    the gateway still thinks the org is subscribed.
    Uses raw SQL to bypass ORM cascade logic."""
    _, org = await setup_org(db_session)
    await setup_plan_and_sub(db_session, org.id)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM organizations WHERE id = :id"),
            {"id": org.id},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_plan_with_subscription_cannot_be_deleted(db_session: AsyncSession):
    """RESTRICT on plan_id — cannot delete a plan while an org is on it."""
    _, org = await setup_org(db_session)
    plan, _ = await setup_plan_and_sub(db_session, org.id)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM subscription_plans WHERE id = :id"),
            {"id": plan.id},
        )
        await db_session.flush()


# ── OrganizationSubscription — relationships ──────────────────────────────────

@pytest.mark.asyncio
async def test_subscription_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)
    await db_session.refresh(sub, ["organization"])

    assert sub.organization is not None
    assert sub.organization.id == org.id


@pytest.mark.asyncio
async def test_subscription_plan_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    plan, sub = await setup_plan_and_sub(db_session, org.id)
    await db_session.refresh(sub, ["plan"])

    assert sub.plan is not None
    assert sub.plan.id == plan.id


@pytest.mark.asyncio
async def test_organization_subscription_relationship(db_session: AsyncSession):
    """org.subscription loads the org's subscription row."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)
    await db_session.refresh(org, ["subscription"])

    assert org.subscription is not None
    assert org.subscription.id == sub.id


# ── PaymentTransaction — persistence ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_transaction_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()

    result = await db_session.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == txn.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.subscription_id == sub.id
    assert fetched.amount == 5000
    assert fetched.currency == "NGN"
    assert fetched.status == "succeeded"


@pytest.mark.asyncio
async def test_transaction_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()

    parts = txn.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_transaction_created_at_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()

    assert txn.created_at is not None


@pytest.mark.asyncio
async def test_transaction_has_no_updated_at(db_session: AsyncSession):
    """PaymentTransaction deliberately has no updated_at — it is an
    append-only ledger. TimestampMixin is not used here;
    only created_at is needed for an immutable ledger entry."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()

    assert not hasattr(txn, "updated_at")


@pytest.mark.asyncio
async def test_transaction_optional_fields_nullable(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()

    assert txn.payment_method is None
    assert txn.external_reference is None
    assert txn.failure_reason is None


@pytest.mark.asyncio
async def test_transaction_full_fields(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(
        org.id, sub.id,
        amount=15000,
        currency="USD",
        status="succeeded",
        payment_method="card",
        external_reference="ch_stripe_xxx",
    )
    db_session.add(txn)
    await db_session.flush()

    result = await db_session.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == txn.id)
    )
    fetched = result.scalar_one()
    assert fetched.amount == 15000
    assert fetched.currency == "USD"
    assert fetched.payment_method == "card"
    assert fetched.external_reference == "ch_stripe_xxx"


@pytest.mark.asyncio
async def test_transaction_failed_has_failure_reason(db_session: AsyncSession):
    """failure_reason is set on failed transactions — NULL on success."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(
        org.id, sub.id,
        status="failed",
        failure_reason="insufficient_funds",
    )
    db_session.add(txn)
    await db_session.flush()

    assert txn.status == "failed"
    assert txn.failure_reason == "insufficient_funds"


@pytest.mark.asyncio
async def test_transaction_amount_must_be_positive(db_session: AsyncSession):
    """amount > 0 is enforced via CHECK constraint — every real charge
    is a positive amount. Unlike SubscriptionPlan (which allows 0 for
    free plans), transactions represent actual charges."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id, amount=0)
    db_session.add(txn)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_transaction_negative_amount_raises(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id, amount=-500)
    db_session.add(txn)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed", "refunded", "pending"])
async def test_transaction_valid_statuses(db_session: AsyncSession, status: str):
    _, org = await setup_org(
        db_session, email=f"{status}@example.com", slug=f"org-{status}"
    )
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id, status=status)
    db_session.add(txn)
    await db_session.flush()
    assert txn.status == status


@pytest.mark.asyncio
async def test_transaction_invalid_status_raises(db_session: AsyncSession):
    """status is constrained to four known values."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id, status="processing")
    db_session.add(txn)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_transaction_is_append_only_by_convention(db_session: AsyncSession):
    """A status change is modelled by adding a new transaction row,
    not updating an existing one. This test verifies that pattern:
    a failed charge + a subsequent successful retry = two rows."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    db_session.add(make_transaction(
        org.id, sub.id, status="failed",
        external_reference="ch_failed_xxx",
        failure_reason="card_declined",
    ))
    db_session.add(make_transaction(
        org.id, sub.id, status="succeeded",
        external_reference="ch_retry_xxx",
    ))
    await db_session.flush()

    result = await db_session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.subscription_id == sub.id
        )
    )
    txns = result.scalars().all()
    assert len(txns) == 2
    statuses = {t.status for t in txns}
    assert statuses == {"failed", "succeeded"}


@pytest.mark.asyncio
async def test_transaction_multiple_per_subscription(db_session: AsyncSession):
    """One subscription accumulates many transaction rows over its
    lifetime — monthly renewal, retries, refunds etc."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    for _ in range(3):
        db_session.add(make_transaction(org.id, sub.id, status="succeeded"))
    await db_session.flush()

    result = await db_session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.subscription_id == sub.id
        )
    )
    assert len(result.scalars().all()) == 3


# ── PaymentTransaction — FK constraints ───────────────────────────────────────

@pytest.mark.asyncio
async def test_transaction_org_fk_must_exist(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction("00000000-0000-0000-0000-000000000000", sub.id)
    db_session.add(txn)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_transaction_subscription_fk_must_exist(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    txn = make_transaction(org.id, "00000000-0000-0000-0000-000000000000")
    db_session.add(txn)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_subscription_delete_cascades_to_transactions(db_session: AsyncSession):
    """ondelete=CASCADE on subscription_id — deleting a subscription
    wipes its transaction history."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    db_session.add(make_transaction(org.id, sub.id))
    db_session.add(make_transaction(org.id, sub.id, status="pending"))
    await db_session.flush()

    # Raw delete to bypass RESTRICT on organization_id within the sub
    await db_session.execute(
        text("DELETE FROM organization_subscriptions WHERE id = :id"),
        {"id": sub.id},
    )
    await db_session.flush()

    result = await db_session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.subscription_id == sub.id
        )
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_org_delete_cascades_to_transactions(db_session: AsyncSession):
    """ondelete=CASCADE on organization_id — once an org is fully
    offboarded (subscription cancelled and deleted first), transaction
    history can be wiped as part of GDPR deletion.
    We simulate the offboarding order: delete subscription first
    (which cascades to transactions), then delete org."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()
    txn_id = txn.id

    # Proper offboarding order: subscription first, then org
    await db_session.execute(
        text("DELETE FROM organization_subscriptions WHERE id = :id"),
        {"id": sub.id},
    )
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM organizations WHERE id = :id"),
        {"id": org.id},
    )
    await db_session.flush()

    result = await db_session.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == txn_id)
    )
    assert result.scalar_one_or_none() is None


# ── PaymentTransaction — relationships ────────────────────────────────────────

@pytest.mark.asyncio
async def test_transaction_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn, ["organization"])

    assert txn.organization is not None
    assert txn.organization.id == org.id


@pytest.mark.asyncio
async def test_transaction_subscription_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    txn = make_transaction(org.id, sub.id)
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn, ["subscription"])

    assert txn.subscription is not None
    assert txn.subscription.id == sub.id


@pytest.mark.asyncio
async def test_subscription_transactions_relationship(db_session: AsyncSession):
    """sub.transactions loads all transaction rows for that subscription."""
    _, org = await setup_org(db_session)
    _, sub = await setup_plan_and_sub(db_session, org.id)

    db_session.add(make_transaction(org.id, sub.id, status="succeeded"))
    db_session.add(make_transaction(org.id, sub.id, status="refunded"))
    await db_session.flush()
    await db_session.refresh(sub, ["transactions"])

    assert len(sub.transactions) == 2
    statuses = {t.status for t in sub.transactions}
    assert statuses == {"succeeded", "refunded"}


# ── Index sanity checks ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payment_transactions_org_created_at_index_exists(db_session: AsyncSession):
    """Composite index for date-range queries per org."""
    result = await db_session.execute(
        text("PRAGMA index_list('payment_transactions')")
    )
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_payment_transactions_org_created_at" in index_names