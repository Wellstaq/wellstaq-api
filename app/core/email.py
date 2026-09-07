"""Transactional email delivery through Brevo's HTTPS API."""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _validate_brevo_settings() -> None:
    required = {
        "BREVO_API_URL": settings.BREVO_API_URL,
        "BREVO_API_KEY": settings.BREVO_API_KEY,
        "MAIL_FROM": settings.MAIL_FROM,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing Brevo HTTP configuration: {', '.join(missing)}")


async def send_email(to: str, subject: str, html: str) -> None:
    """Send one transactional email over HTTPS without blocking the event loop."""
    _validate_brevo_settings()

    payload = {
        "sender": {
            "email": settings.MAIL_FROM,
            "name": settings.MAIL_FROM_NAME,
        },
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {
        "accept": "application/json",
        "api-key": settings.BREVO_API_KEY,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.BREVO_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                settings.BREVO_API_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.error(
            "Brevo email API rejected request",
            extra={"to": to, "status_code": status_code},
        )
        raise RuntimeError(
            f"Brevo email API rejected request with status {status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("Brevo email API request failed", extra={"to": to})
        raise RuntimeError("Brevo email API request failed") from exc

    try:
        message_id = response.json().get("messageId")
    except ValueError as exc:
        raise RuntimeError("Brevo email API returned an invalid response") from exc
    if not message_id:
        raise RuntimeError("Brevo email API response did not include a message ID")

    logger.info("Brevo email accepted", extra={"to": to, "message_id": message_id})
