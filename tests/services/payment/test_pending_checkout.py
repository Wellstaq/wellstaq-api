import pytest
from fastapi import HTTPException

from app.models import PaymentTransaction
from app.services.payment.pending_checkout import cancel_pending_checkout
from app.services.payment.webhook_service import handle_charge_success
from tests.services.payment.test_payment_flow import setup_billing


async def pending(db_session):
    org, owner, plan, subscription = await setup_billing(db_session)
    tx = PaymentTransaction(organization_id=org.id, subscription_id=subscription.id,
                            plan_id=plan.id, amount=plan.amount, currency=plan.currency,
                            external_reference="cancel-test", status="pending")
    db_session.add(tx)
    await db_session.flush()
    return org, tx, subscription


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", ["abandoned", "failed"])
async def test_unpaid_checkout_can_be_cancelled(db_session, monkeypatch, provider_status):
    org, tx, _ = await pending(db_session)
    async def verify(**kwargs):
        return {"reference": tx.external_reference, "status": provider_status}
    monkeypatch.setattr("app.services.payment.pending_checkout.verify_transaction", verify)
    result = await cancel_pending_checkout(session=db_session, organization_id=org.id, reference=tx.external_reference)
    assert result.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", ["pending", "ongoing", "processing", "queued", "unknown"])
async def test_processing_checkout_is_not_released(db_session, monkeypatch, provider_status):
    org, tx, _ = await pending(db_session)
    async def verify(**kwargs):
        return {"reference": tx.external_reference, "status": provider_status}
    monkeypatch.setattr("app.services.payment.pending_checkout.verify_transaction", verify)
    with pytest.raises(HTTPException) as error:
        await cancel_pending_checkout(session=db_session, organization_id=org.id, reference=tx.external_reference)
    assert error.value.status_code == 409
    assert tx.status == "pending"


@pytest.mark.asyncio
async def test_late_success_after_cancel_is_reconciled_once(db_session, monkeypatch):
    org, tx, subscription = await pending(db_session)
    async def verify(**kwargs):
        return {"reference": tx.external_reference, "status": "abandoned"}
    monkeypatch.setattr("app.services.payment.pending_checkout.verify_transaction", verify)
    await cancel_pending_checkout(session=db_session, organization_id=org.id, reference=tx.external_reference)
    event = {"reference": tx.external_reference, "amount": tx.amount, "currency": tx.currency,
             "paid_at": "2026-10-20T12:00:00Z"}
    await handle_charge_success(session=db_session, event_data=event)
    assert tx.status == "succeeded" and subscription.status == "active"
    first_end = subscription.current_period_end
    await handle_charge_success(session=db_session, event_data=event)
    assert subscription.current_period_end == first_end


@pytest.mark.asyncio
async def test_paid_checkout_is_reconciled_instead_of_cancelled(db_session, monkeypatch):
    org, tx, subscription = await pending(db_session)
    async def verify(**kwargs):
        return {"reference": tx.external_reference, "status": "success", "amount": tx.amount, "currency": tx.currency}
    monkeypatch.setattr("app.services.payment.pending_checkout.verify_transaction", verify)
    result = await cancel_pending_checkout(session=db_session, organization_id=org.id, reference=tx.external_reference)
    assert result.status == "succeeded" and subscription.status == "active"


@pytest.mark.asyncio
async def test_other_organization_cannot_cancel_checkout(db_session):
    _, tx, _ = await pending(db_session)
    with pytest.raises(HTTPException) as error:
        await cancel_pending_checkout(session=db_session, organization_id="different-org", reference=tx.external_reference)
    assert error.value.status_code == 404
