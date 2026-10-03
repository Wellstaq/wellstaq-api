import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.payment import OrganizationSubscription, PaymentTransaction, SubscriptionPlan
from tests.conftest import make_org_with_owner


def signed_headers(body: bytes) -> dict[str, str]:
    signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512
    ).hexdigest()
    return {"x-paystack-signature": signature, "content-type": "application/json"}


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(client):
    response = await client.post(
        "/webhooks/paystack",
        content=b'{"event":"charge.success","data":{}}',
        headers={"x-paystack-signature": "invalid"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_signed_invalid_json(client):
    body = b"not-json"
    response = await client.post(
        "/webhooks/paystack", content=body, headers=signed_headers(body)
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_invoice_update_reverifies_and_records_renewal(
    client, db_session, monkeypatch
):
    org, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "invoice-renewal"},
        user_kwargs={"email": "invoice-renewal@example.com"},
    )
    plan = SubscriptionPlan(
        name="Invoice plan", billing_interval="monthly", amount=250_000,
        currency="NGN", features={}, is_active=True, external_plan_id="PLN_invoice",
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
    sub.status = "active"
    sub.auto_renew = True
    sub.external_subscription_id = "SUB_invoice"
    await db_session.flush()

    next_date = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)

    async def fake_verify(*, reference):
        assert reference == "invoice-renewal-ref"
        return {
            "reference": reference,
            "status": "success",
            "amount": plan.amount,
            "currency": plan.currency,
            "paid_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr("app.routers.payments.verify_transaction", fake_verify)
    event = {
        "event": "invoice.update",
        "data": {
            "paid": True,
            "status": "success",
            "period_start": datetime.now(timezone.utc).isoformat(),
            "subscription": {
                "subscription_code": "SUB_invoice",
                "next_payment_date": next_date.isoformat(),
            },
            "transaction": {
                "reference": "invoice-renewal-ref",
                "status": "success",
                "amount": plan.amount,
                "currency": plan.currency,
            },
        },
    }
    body = json.dumps(event).encode()
    response = await client.post(
        "/webhooks/paystack", content=body, headers=signed_headers(body)
    )
    assert response.status_code == 200
    transaction = (
        await db_session.execute(
            select(PaymentTransaction).where(
                PaymentTransaction.external_reference == "invoice-renewal-ref"
            )
        )
    ).scalar_one()
    assert transaction.status == "succeeded"
    assert sub.current_period_end == next_date


@pytest.mark.asyncio
async def test_charge_event_with_provider_status_mismatch_is_ignored(
    client, monkeypatch
):
    async def fake_verify(*, reference):
        return {"reference": reference, "status": "failed"}

    monkeypatch.setattr("app.routers.payments.verify_transaction", fake_verify)
    event = {
        "event": "charge.success",
        "data": {"reference": "does-not-match", "amount": 100, "currency": "NGN"},
    }
    body = json.dumps(event).encode()
    response = await client.post(
        "/webhooks/paystack", content=body, headers=signed_headers(body)
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}

