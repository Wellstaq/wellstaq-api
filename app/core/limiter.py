"""
app/core/limiter.py
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,  # fallback default for routes with no key_func override
    storage_uri=settings.REDIS_URL,
)


def key_by_email_or_ip(request: Request) -> str:
    """
    Sync key_func. Reads the email a dependency already parsed and
    stashed on request.state (see extract_email_for_rate_limit).
    Falls back to IP if no email was found on the body.
    """
    email = getattr(request.state, "rate_limit_email", None)
    if email:
        return f"email:{email.lower()}"
    return f"ip:{get_remote_address(request)}"


async def extract_email_for_rate_limit(request: Request) -> None:
    """
    FastAPI dependency — parses the body once, stashes email on
    request.state. Runs before slowapi's limit check since FastAPI
    resolves route dependencies before executing the decorated route.
    """
    try:
        body = await request.json()
        request.state.rate_limit_email = body.get("email")
    except Exception:
        request.state.rate_limit_email = None