from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.payment import OrganizationSubscription, PaymentTransaction, SubscriptionPlan
from app.services.payment.checkout_service import initiate_checkout
from app.services.payment.webhook_service import (
    handle_charge_success,
    handle_invoice_payment_failed,
    handle_subscription_create,
    handle_subscription_disable,
    handle_subscription_not_renewing,
)
from tests.conftest import make_org_with_owner


async def setup_billing(db_session, *, status="trialing", active_for_days=7):
    token = uuid4().hex
    org, owner = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": f"billing-{status}-{token}"},
        user_kwargs={"email": f"billing-{status}-{token}@example.com"},
    )
    plan = SubscriptionPlan(
        name=f"Paid {token}",
        billing_interval="monthly",
        amount=500_000,
        currency="NGN",
        features={},
        is_active=True,
        external_plan_id="PLN_paid",
    )
    db_session.add(plan)
    await db_session.flush()
    sub = (
        await db_session.execute(
            select(OrganizationSubscription).where(
                OrganizationSubscription.organization_id == org.id
            )
        )
    ).scalar_one()
    sub.plan_id = plan.id
    sub.status = status
    sub.current_period_end = datetime.now(timezone.utc) + timedelta(days=active_for_days)
    await db_session.flush()
    return org, owner, plan, sub


@pytest.mark.asyncio
async def test_checkout_rejects_unexpired_active_subscription(db_session, monkeypatch):
    org, owner, plan, _ = await setup_billing(db_session, status="active")
    called = False

    async def fake_initialize(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("app.services.payment.checkout_service.initialize_transaction", fake_initialize)
    with pytest.raises(HTTPException) as exc:
        await initiate_checkout(
            session=db_session, organization_id=org.id, plan_id=plan.id,
            initiating_user=owner,
        )
    assert exc.value.status_code == 409
    assert called is False


@pytest.mark.asyncio
async def test_checkout_allows_expired_active_subscription_and_sends_metadata(db_session, monkeypatch):
    org, owner, plan, sub = await setup_billing(
        db_session, status="active", active_for_days=-1
    )
    captured = {}

    async def fake_initialize(**kwargs):
        captured.update(kwargs)
        return {"authorization_url": "https://checkout.test/x", "access_code": "ac"}

    monkeypatch.setattr("app.services.payment.checkout_service.initialize_transaction", fake_initialize)
    response = await initiate_checkout(
        session=db_session, organization_id=org.id, plan_id=plan.id,
        initiating_user=owner, auto_renew=True,
    )
    assert response["authorization_url"] == "https://checkout.test/x"
    assert captured["plan_code"] == "PLN_paid"
    assert captured["metadata"] == {
        "organization_id": org.id,
        "subscription_id": sub.id,
        "plan_id": plan.id,
        "auto_renew": True,
    }


@pytest.mark.asyncio
async def test_checkout_blocks_pending_transaction_for_other_plan(db_session, monkeypatch):
    org, owner, plan, sub = await setup_billing(db_session)
    other = SubscriptionPlan(
        name=f"Other {datetime.now().timestamp()}", billing_interval="annual",
        amount=900_000, currency="NGN", features={}, is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(PaymentTransaction(
        organization_id=org.id, subscription_id=sub.id, plan_id=other.id,
        amount=other.amount, currency=other.currency, status="pending",
        external_reference=f"pending-{datetime.now().timestamp()}",
    ))
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await initiate_checkout(
            session=db_session, organization_id=org.id, plan_id=plan.id,
            initiating_user=owner,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_database_enforces_one_pending_checkout_per_organization(db_session):
    org, _, plan, sub = await setup_billing(db_session)
    for suffix in ("one", "two"):
        db_session.add(PaymentTransaction(
            organization_id=org.id, subscription_id=sub.id, plan_id=plan.id,
            amount=plan.amount, currency=plan.currency, status="pending",
            external_reference=f"{suffix}-{datetime.now().timestamp()}",
        ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_successful_initial_charge_activates_and_captures_gateway_ids(db_session):
    org, _, plan, sub = await setup_billing(db_session)
    txn = PaymentTransaction(
        organization_id=org.id, subscription_id=sub.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status="pending",
        external_reference="initial-success",
    )
    db_session.add(txn)
    await db_session.flush()
    paid_at = datetime.now(timezone.utc).replace(microsecond=0)
    next_date = paid_at + timedelta(days=30)

    await handle_charge_success(session=db_session, event_data={
        "reference": "initial-success", "amount": plan.amount, "currency": "NGN",
        "paid_at": paid_at.isoformat(), "channel": "card",
        "customer": {"customer_code": "CUS_123"},
        "subscription": {
            "subscription_code": "SUB_123", "email_token": "token",
            "next_payment_date": next_date.isoformat(),
        },
    })
    assert txn.status == "succeeded"
    assert sub.status == "active"
    assert sub.current_period_end == next_date
    assert sub.external_customer_id == "CUS_123"
    assert sub.external_subscription_id == "SUB_123"
    assert sub.email_token == "token"
    assert sub.auto_renew is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount", "currency", "reason"),
    [(499_999, "NGN", "Amount mismatch"), (500_000, None, "Currency mismatch")],
)
async def test_mismatched_charge_never_activates(db_session, amount, currency, reason):
    org, _, plan, sub = await setup_billing(db_session)
    txn = PaymentTransaction(
        organization_id=org.id, subscription_id=sub.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status="pending",
        external_reference=f"mismatch-{reason}",
    )
    db_session.add(txn)
    await db_session.flush()

    await handle_charge_success(session=db_session, event_data={
        "reference": txn.external_reference, "amount": amount, "currency": currency,
    })
    assert txn.status == "failed"
    assert reason in txn.failure_reason
    assert sub.status == "trialing"


@pytest.mark.asyncio
async def test_mismatched_gateway_plan_never_activates(db_session):
    org, _, plan, sub = await setup_billing(db_session)
    txn = PaymentTransaction(
        organization_id=org.id, subscription_id=sub.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status="pending",
        external_reference="wrong-plan",
    )
    db_session.add(txn)
    await db_session.flush()
    await handle_charge_success(session=db_session, event_data={
        "reference": "wrong-plan", "amount": plan.amount, "currency": plan.currency,
        "plan": {"plan_code": "PLN_wrong"},
    })
    assert txn.status == "failed"
    assert "Plan mismatch" in txn.failure_reason
    assert sub.status == "trialing"


@pytest.mark.asyncio
async def test_duplicate_success_event_is_idempotent(db_session):
    org, _, plan, sub = await setup_billing(db_session)
    txn = PaymentTransaction(
        organization_id=org.id, subscription_id=sub.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status="succeeded",
        external_reference="already-done",
    )
    db_session.add(txn)
    await db_session.flush()
    old_end = sub.current_period_end
    await handle_charge_success(session=db_session, event_data={
        "reference": "already-done", "amount": plan.amount, "currency": "NGN",
    })
    assert sub.current_period_end == old_end


@pytest.mark.asyncio
async def test_recurring_charge_creates_ledger_entry_and_extends_period(db_session):
    org, _, plan, sub = await setup_billing(db_session, status="active", active_for_days=0)
    sub.external_subscription_id = "SUB_RENEW"
    sub.auto_renew = True
    next_date = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
    await db_session.flush()

    await handle_charge_success(session=db_session, event_data={
        "reference": "renewal-reference", "amount": plan.amount, "currency": "NGN",
        "paid_at": datetime.now(timezone.utc).isoformat(),
        "subscription": {
            "subscription_code": "SUB_RENEW",
            "next_payment_date": next_date.isoformat(),
        },
    })
    txn = (
        await db_session.execute(
            select(PaymentTransaction).where(
                PaymentTransaction.external_reference == "renewal-reference"
            )
        )
    ).scalar_one()
    assert txn.status == "succeeded"
    assert txn.subscription_id == sub.id
    assert sub.current_period_end == next_date
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_unknown_successful_charge_does_not_create_unowned_transaction(db_session):
    await handle_charge_success(session=db_session, event_data={
        "reference": "unknown", "amount": 100, "currency": "NGN",
    })
    assert (
        await db_session.execute(
            select(PaymentTransaction).where(PaymentTransaction.external_reference == "unknown")
        )
    ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_subscription_create_can_map_by_customer_email(db_session):
    _, owner, _, sub = await setup_billing(db_session)
    next_date = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
    await handle_subscription_create(session=db_session, event_data={
        "subscription_code": "SUB_CREATED", "email_token": "mail-token",
        "next_payment_date": next_date.isoformat(),
        "customer": {"email": owner.email, "customer_code": "CUS_CREATED"},
    })
    assert sub.external_subscription_id == "SUB_CREATED"
    assert sub.external_customer_id == "CUS_CREATED"
    assert sub.email_token == "mail-token"
    assert sub.auto_renew is True
    assert sub.current_period_end == next_date


@pytest.mark.asyncio
async def test_failed_invoice_marks_subscription_past_due(db_session):
    _, _, _, sub = await setup_billing(db_session, status="active")
    sub.external_subscription_id = "SUB_FAILED"
    await db_session.flush()
    await handle_invoice_payment_failed(
        session=db_session,
        event_data={"subscription": {"subscription_code": "SUB_FAILED"}},
    )
    assert sub.status == "past_due"


@pytest.mark.asyncio
async def test_not_renewing_preserves_access_but_disables_auto_renew(db_session):
    _, _, _, sub = await setup_billing(db_session, status="active")
    sub.external_subscription_id = "SUB_STOP"
    sub.auto_renew = True
    await db_session.flush()
    await handle_subscription_not_renewing(
        session=db_session,
        event_data={"subscription_code": "SUB_STOP"},
    )
    assert sub.status == "active"
    assert sub.auto_renew is False


@pytest.mark.asyncio
async def test_subscription_disable_cancels_and_stamps_subscription(db_session):
    _, _, _, sub = await setup_billing(db_session, status="active")
    sub.external_subscription_id = "SUB_DISABLED"
    sub.auto_renew = True
    await db_session.flush()
    await handle_subscription_disable(
        session=db_session,
        event_data={"subscription_code": "SUB_DISABLED"},
    )
    assert sub.status == "cancelled"
    assert sub.auto_renew is False
    assert sub.cancelled_at is not None
