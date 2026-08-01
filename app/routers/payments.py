"""
app/routers/payments.py

Two router objects, registered separately in app/main.py:
  - router:         org-scoped billing routes (/organizations/{org_id}/...)
  - webhook_router:  global Paystack webhook receiver (/webhooks/paystack) —
                      no org_id in the path, since Paystack has no concept
                      of org structure; the org is identified from
                      inside the webhook payload instead.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_permission, require_same_organization, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import User
from app.schemas.payment import (
    CheckoutRequest,
    CheckoutResponse,
    SubscriptionResponse,
    TransactionListResponse,
    TransactionResponse,
    PlanListResponse,
    PlanResponse
)
from app.services.payment import (get_subscription, 
    get_transaction_history, 
    initiate_checkout,
    cancel_auto_renew, 
    get_available_plans,
    handle_charge_failed,
    verify_paystack_signature,
    handle_charge_success,  
    
)

from app.services.payment.paystack_client import verify_transaction, PaystackError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organizations/{org_id}", tags=["payment"])


@router.get("/subscription", response_model=SubscriptionResponse, status_code=status.HTTP_200_OK)
async def get_subscription_route(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("org.billing.manage")),
) -> SubscriptionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/subscription"):
        subscription = await get_subscription(session=db, organization_id=org_id)
        return SubscriptionResponse.model_validate(subscription)


@router.get("/transactions", response_model=TransactionListResponse, status_code=status.HTTP_200_OK)
async def list_transactions_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("org.billing.manage")),
) -> TransactionListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/transactions"):
        transactions, total = await get_transaction_history(session=db, organization_id=org_id, offset=offset, limit=limit)
        return TransactionListResponse(items=[TransactionResponse.model_validate(t) for t in transactions], total=total, offset=offset, limit=limit)


@router.post("/checkout", response_model=CheckoutResponse, status_code=status.HTTP_200_OK)
async def initiate_checkout_route(
    org_id: str, body: CheckoutRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("org.billing.manage")),
) -> CheckoutResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/checkout"):
        user_result = await db.execute(select(User).where(User.id == current_user.user_id))
        user = user_result.scalar_one()

        result = await initiate_checkout(
            session=db, organization_id=org_id, plan_id=body.plan_id, initiating_user=user,
            auto_renew=body.auto_renew, callback_url=body.callback_url,
        )
        await db.commit()
        return CheckoutResponse(**result)

@router.post("/subscription/cancel-auto-renew", response_model=SubscriptionResponse, status_code=status.HTTP_200_OK)
async def cancel_auto_renew_route(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("org.billing.manage")),
) -> SubscriptionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/subscription/cancel-auto-renew"):
        subscription = await cancel_auto_renew(session=db, organization_id=org_id)
        await db.commit()
        return SubscriptionResponse.model_validate(subscription)


# ── Webhook — separate router, no org_id prefix ─────────────────────────────

webhook_router = APIRouter(tags=["payment-webhooks"])

@webhook_router.post("/webhooks/paystack", status_code=status.HTTP_200_OK)
async def paystack_webhook_route(request: Request, db: AsyncSession = Depends(get_db)):
    payload_body = await request.body()
    signature = request.headers.get("x-paystack-signature")

    if not verify_paystack_signature(payload_body=payload_body, signature_header=signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature.")

    event = json.loads(payload_body)
    event_type = event.get("event")
    event_data = event.get("data", {})

    # ── Server-to-server re-verification ────────────────────────────────────
    # Signature verification confirms the payload genuinely came from
    # Paystack right now — it does NOT independently confirm the payment
    # actually happened on Paystack's records. Re-querying Paystack
    # directly for the transaction's true status is a second, independent
    # source of truth, defending against any scenario where a signed
    # payload doesn't match Paystack's actual ledger.
    reference = event_data.get("reference")
    if event_type in ("charge.success", "charge.failed") and reference:
        try:
            verified_data = await verify_transaction(reference=reference)
        except PaystackError:
            logger.error("Failed to re-verify transaction with Paystack", extra={"reference": reference})
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not verify transaction with payment provider.")

        if verified_data.get("status") != ("success" if event_type == "charge.success" else "failed"):
            logger.error(
                "Webhook event type does not match Paystack's verified status — ignoring",
                extra={"reference": reference, "webhook_event": event_type, "verified_status": verified_data.get("status")},
            )
            return {"status": "ignored"}

    async with request_guard(db, "/webhooks/paystack"):
        if event_type == "charge.success":
            await handle_charge_success(session=db, event_data=event_data)
        elif event_type == "charge.failed":
            await handle_charge_failed(session=db, event_data=event_data)
        elif event_type == "invoice.payment_failed":
            await handle_invoice_payment_failed(session=db, event_data=event_data)
        elif event_type == "subscription.disable":
            await handle_subscription_disable(session=db, event_data=event_data)
        else:
            logger.info("Unhandled Paystack event type", extra={"event_type": event_type})

        await db.commit()

    return {"status": "received"}
# ── platform subscription plans — separate router, no org_id prefix ─────────────────────────────

plans_router = APIRouter(tags=["payment"])

@plans_router.get("/plans", response_model=PlanListResponse, status_code=status.HTTP_200_OK)
async def list_available_plans_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PlanListResponse:
    """No org scoping — plans are a global, platform-wide catalogue,
    same as WellbeingDimension. Registered outside the org-prefixed
    router since it isn't org-specific data."""
    async with request_guard(db, "/plans"):
        plans = await get_available_plans(session=db)
        return PlanListResponse(items=[PlanResponse.model_validate(p) for p in plans])
