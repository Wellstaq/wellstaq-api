"""
app/services/payment/paystack_client.py

Thin wrapper around Paystack's REST API. No business logic here — just
HTTP calls and response shaping. Business logic (what to do with a
successful charge) lives in webhook_service.py.

Paystack docs: https://paystack.com/docs/api/
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


async def initialize_transaction(*, email: str, amount: int, currency: str, reference: str, callback_url: str | None = None) -> dict:
    """
    amount must already be in the smallest currency unit (kobo for NGN),
    matching PaymentTransaction.amount's convention exactly — no
    conversion happens here.

    Returns Paystack's response data, including 'authorization_url' —
    the checkout page to redirect the user to.
    """
    payload = {"email": email, "amount": amount, "currency": currency, "reference": reference}
    if callback_url:
        payload["callback_url"] = callback_url

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(f"{PAYSTACK_BASE_URL}/transaction/initialize", json=payload, headers=_headers())
        except httpx.RequestError as exc:
            logger.error("Paystack initialize request failed", exc_info=True)
            raise PaystackError(f"Could not reach Paystack: {exc}") from exc

    body = response.json()
    if not body.get("status"):
        raise PaystackError(body.get("message", "Paystack transaction initialization failed."), response.status_code)

    return body["data"]


async def verify_transaction(*, reference: str) -> dict:
    """Server-to-server verification — used as a fallback/manual check,
    NOT the primary confirmation path (that's the webhook, see
    webhook_service.py). Useful for a 'check payment status' button if
    a webhook is delayed."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}", headers=_headers())
        except httpx.RequestError as exc:
            logger.error("Paystack verify request failed", exc_info=True)
            raise PaystackError(f"Could not reach Paystack: {exc}") from exc

    body = response.json()
    if not body.get("status"):
        raise PaystackError(body.get("message", "Paystack transaction verification failed."), response.status_code)

    return body["data"]


async def initialize_transaction(
    *, email: str, amount: int, currency: str, reference: str,
    plan_code: str | None = None, callback_url: str | None = None,
) -> dict:
    payload = {"email": email, "amount": amount, "currency": currency, "reference": reference}
    if callback_url:
        payload["callback_url"] = callback_url
    if plan_code:
        payload["plan"] = plan_code  # attaching a plan is what creates a recurring subscription, not just a one-time charge

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(f"{PAYSTACK_BASE_URL}/transaction/initialize", json=payload, headers=_headers())
        except httpx.RequestError as exc:
            raise PaystackError(f"Could not reach Paystack: {exc}") from exc

    body = response.json()
    if not body.get("status"):
        raise PaystackError(body.get("message", "Paystack transaction initialization failed."), response.status_code)
    return body["data"]


# paystack_client.py — new function
async def disable_subscription(*, subscription_code: str, email_token: str) -> None:
    """email_token is Paystack's per-subscription token, returned when
    the subscription was created — needed to authorize disabling it."""
    payload = {"code": subscription_code, "token": email_token}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(f"{PAYSTACK_BASE_URL}/subscription/disable", json=payload, headers=_headers())
    body = response.json()
    if not body.get("status"):
        raise PaystackError(body.get("message", "Failed to disable subscription."), response.status_code)