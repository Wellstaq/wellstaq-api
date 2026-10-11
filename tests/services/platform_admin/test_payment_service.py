"""Service tests for app/services/platform_admin/payment_service.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.payment import OrganizationSubscription, PaymentTransaction, SubscriptionPlan
from app.services.platform_admin import payment_service
from tests.conftest import make_org_with_owner


async def _setup_billing(db_session, *, token: str | None = None):
    token = token or uuid4().hex
    org, owner = await make_org_with_owner(
        db_session,
        org_kwargs={"business_name": f"Acme {token}", "slug": f"acme-{token}"},
        user_kwargs={"email": f"owner-{token}@example.com"},
    )
    plan = SubscriptionPlan(
        name=f"Paid {token}", billing_interval="monthly", amount=500_000,
        currency="NGN", features={}, is_active=True, external_plan_id=f"PLN_{token}",
    )
    db_session.add(plan)
    await db_session.flush()
    from sqlalchemy import select
    sub = (await db_session.execute(select(OrganizationSubscription).where(OrganizationSubscription.organization_id == org.id))).scalar_one()
    sub.plan_id = plan.id
    await db_session.flush()
    return org, owner, plan, sub


def _make_transaction(org, sub, plan, *, reference: str, status: str = "succeeded") -> PaymentTransaction:
    return PaymentTransaction(
        organization_id=org.id, subscription_id=sub.id, plan_id=plan.id,
        amount=plan.amount, currency=plan.currency, status=status,
        external_reference=reference,
    )


@pytest.mark.asyncio
async def test_list_org_transactions_returns_only_that_orgs_rows(db_session):
    org, _owner, plan, sub = await _setup_billing(db_session)
    other_org, _other_owner, other_plan, other_sub = await _setup_billing(db_session)
    db_session.add(_make_transaction(org, sub, plan, reference="ref-mine"))
    db_session.add(_make_transaction(other_org, other_sub, other_plan, reference="ref-other"))
    await db_session.flush()

    items, total = await payment_service.list_org_transactions(session=db_session, org_id=org.id, offset=0, limit=20)

    assert total == 1
    assert items[0]["external_reference"] == "ref-mine"


@pytest.mark.asyncio
async def test_list_org_transactions_not_found(db_session):
    with pytest.raises(HTTPException) as exc:
        await payment_service.list_org_transactions(session=db_session, org_id="does-not-exist", offset=0, limit=20)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_search_transactions_matches_by_external_reference(db_session):
    org, _owner, plan, sub = await _setup_billing(db_session)
    db_session.add(_make_transaction(org, sub, plan, reference="PSK_abc123xyz"))
    await db_session.flush()

    items, total = await payment_service.search_transactions(session=db_session, q="abc123", offset=0, limit=20)

    assert total == 1
    assert items[0]["organization_id"] == org.id
    assert items[0]["organization_name"] == org.business_name


@pytest.mark.asyncio
async def test_search_transactions_matches_by_internal_id(db_session):
    org, _owner, plan, sub = await _setup_billing(db_session)
    txn = _make_transaction(org, sub, plan, reference="PSK_other")
    db_session.add(txn)
    await db_session.flush()

    items, total = await payment_service.search_transactions(session=db_session, q=txn.id, offset=0, limit=20)

    assert total == 1
    assert items[0]["id"] == txn.id


@pytest.mark.asyncio
async def test_search_transactions_no_match_returns_empty(db_session):
    items, total = await payment_service.search_transactions(session=db_session, q="nonexistent-reference", offset=0, limit=20)
    assert total == 0
    assert items == []
