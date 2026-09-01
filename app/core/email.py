"""Transactional email delivery through an authenticated SMTP relay."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings

logger = logging.getLogger(__name__)


def _validate_smtp_settings() -> None:
    required = {
        "SMTP_HOST": settings.SMTP_HOST,
        "SMTP_USERNAME": settings.SMTP_USERNAME,
        "SMTP_PASSWORD": settings.SMTP_PASSWORD,
        "MAIL_FROM": settings.MAIL_FROM,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing SMTP configuration: {', '.join(missing)}")
    if settings.SMTP_USE_TLS and settings.SMTP_USE_SSL:
        raise RuntimeError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled")


def _send_email_sync(to: str, subject: str, html: str) -> None:
    """Send one HTML email using the configured SMTP relay."""
    _validate_smtp_settings()

    message = EmailMessage()
    message["From"] = formataddr((settings.MAIL_FROM_NAME, settings.MAIL_FROM))
    message["To"] = to
    message["Subject"] = subject
    message.set_content("This email requires an HTML-capable email client.")
    message.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    client: smtplib.SMTP | smtplib.SMTP_SSL
    if settings.SMTP_USE_SSL:
        client = smtplib.SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
            context=context,
        )
    else:
        client = smtplib.SMTP(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
        )

    try:
        client.ehlo()
        if settings.SMTP_USE_TLS:
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message)
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        logger.exception("SMTP email send failed")
        raise


async def send_email(to: str, subject: str, html: str) -> None:
    """Send transactional email without blocking the FastAPI event loop."""
    await asyncio.to_thread(_send_email_sync, to, subject, html)
