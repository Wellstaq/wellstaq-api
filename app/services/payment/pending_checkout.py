"""Release unpaid checkout attempts only after checking the payment provider."""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import PaymentTransaction
from app.services.payment.paystack_client import PaystackError, verify_transaction
from app.services.payment.webhook_service import handle_charge_success


async def cancel_pending_checkout(*, session: AsyncSession, organization_id: str, reference: str):
    transaction = (await session.execute(select(PaymentTransaction).where(
        PaymentTransaction.organization_id == organization_id,
        PaymentTransaction.external_reference == reference,
    ).with_for_update())).scalar_one_or_none()
    if transaction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found for this organization.")
    if transaction.status != "pending":
        return transaction
    try:
        verified = await verify_transaction(reference=reference)
    except PaystackError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "We couldn't check this payment. Try again before starting another payment.") from exc
    if verified.get("reference") != reference:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "We couldn't confirm this payment safely. Try again.")
    provider_status = str(verified.get("status", "")).lower()
    if provider_status == "success":
        await handle_charge_success(session=session, event_data=verified)
    elif provider_status in {"abandoned", "failed"}:
        transaction.status = "cancelled"
        transaction.failure_reason = "Unpaid checkout cancelled by the organization after provider verification."
        await session.flush()
    else:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This payment is still processing. Check its status before starting another payment.")
    return transaction
