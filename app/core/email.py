# app/core/email.py

import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, html: str) -> None:
    """
    Fire-and-forget transactional email via SendGrid.
    Raises on failure so callers can decide how to handle it.
    """
    message = Mail(
        from_email=settings.MAIL_FROM,
        to_emails=to,
        subject=subject,
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"SendGrid rejected email with status {response.status_code}")
        logger.info("SendGrid accepted email", extra={"to": to, "status_code": response.status_code})
    except Exception as exc:
        logger.error("SendGrid send failed", extra={"to": to, "error": str(exc)})
        raise
