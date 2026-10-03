"""Small, defensive async client for the Paystack REST API."""

from __future__ import annotations

import httpx

from app.core.config import settings

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}


async def _request(method: str, path: str, *, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.request(method, f"{PAYSTACK_BASE_URL}{path}", json=payload, headers=_headers())
        except httpx.RequestError as exc:
            raise PaystackError(f"Could not reach Paystack: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise PaystackError("Paystack returned an invalid response.", response.status_code) from exc
    if not response.is_success or not body.get("status"):
        raise PaystackError(body.get("message", "Paystack request failed."), response.status_code)
    return body["data"]


async def initialize_transaction(*, email: str, amount: int, currency: str, reference: str, plan_code: str | None = None, callback_url: str | None = None, metadata: dict | None = None) -> dict:
    payload: dict[str, object] = {"email": email, "amount": amount, "currency": currency, "reference": reference}
    if callback_url:
        payload["callback_url"] = callback_url
    if plan_code:
        payload["plan"] = plan_code
    if metadata:
        payload["metadata"] = metadata
    return await _request("POST", "/transaction/initialize", payload=payload)


async def verify_transaction(*, reference: str) -> dict:
    return await _request("GET", f"/transaction/verify/{reference}")


async def disable_subscription(*, subscription_code: str, email_token: str) -> None:
    await _request("POST", "/subscription/disable", payload={"code": subscription_code, "token": email_token})


async def enable_subscription(*, subscription_code: str, email_token: str) -> None:
    await _request("POST", "/subscription/enable", payload={"code": subscription_code, "token": email_token})
