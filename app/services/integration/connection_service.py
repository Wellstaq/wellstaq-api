"""
app/services/integration/connection_service.py

Personal (user-level) OAuth connect/callback/list/disconnect — the
employee app's "Connect Google Fit" flow lives here, and any future
provider's equivalent flow reuses the same functions (nothing here is
Google-specific; see providers.py to register a new one). Distinct from
the org-level OrganizationIntegration catalogue in catalogue.py (an
admin-facing feature toggle, e.g. "allow Google Calendar sync for this
org"); this is each individual user authorizing their own account.

The callback is hit directly by the provider's browser redirect, not
called by the frontend as a JSON API — it returns a tiny HTML page
rather than a JSON body, matching the standard "OAuth popup" pattern:
the frontend opens build_authorization_url's result in a popup/new tab,
this page tells the user it's done, and the frontend's own window
re-fetches /me/integrations on focus/interval rather than listening for
a message.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenError, create_oauth_state_token, decode_oauth_state_token
from app.models import UserIntegration
from app.services.integration.crypto import IntegrationNotConfigured, encrypt_token
from app.services.integration.oauth_client import build_authorization_url, exchange_code_for_tokens
from app.services.integration.providers import PROVIDERS

SUPPORTED_USER_PROVIDERS = tuple(PROVIDERS.keys())

logger = logging.getLogger(__name__)


def start_user_connect(*, user_id: str, organization_id: str, provider: str) -> str:
    if provider not in SUPPORTED_USER_PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{provider}' is not a supported personal integration.")
    state = create_oauth_state_token(user_id=user_id, organization_id=organization_id, provider=provider, kind="user")
    try:
        return build_authorization_url(provider, state)
    except IntegrationNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


async def handle_oauth_callback(
    *, session: AsyncSession, provider: str, code: str | None, state: str | None, error: str | None,
) -> HTMLResponse:
    """Shared callback for both org-level and user-level connects on any
    provider — kind in the decoded state says which, provider in the URL
    path says to whom. Always returns HTML (this is a browser redirect
    target, never a JSON API consumer)."""
    if error:
        return _result_page(f"Sign-in was cancelled or failed: {error}")
    if not code or not state:
        return _result_page("This sign-in link is missing required parameters.")

    try:
        claims = decode_oauth_state_token(state)
    except TokenError:
        return _result_page("This sign-in link has expired or is invalid. Please try connecting again.")

    if claims.get("provider") != provider:
        return _result_page("This sign-in link doesn't match the provider it was issued for.")

    try:
        tokens = await exchange_code_for_tokens(provider, code)
    except (HTTPException, IntegrationNotConfigured) as exc:
        logger.error("OAuth token exchange failed", extra={"user_id": claims.get("sub"), "provider": provider})
        return _result_page(f"Could not complete sign-in: {exc}")

    user_id = claims["sub"]
    organization_id = claims["org_id"]
    kind = claims.get("kind", "user")

    expires_at = datetime.now(timezone.utc)
    if tokens.get("expires_in"):
        from datetime import timedelta
        expires_at = expires_at + timedelta(seconds=tokens["expires_in"])

    if kind == "user":
        result = await session.execute(
            select(UserIntegration).where(UserIntegration.user_id == user_id, UserIntegration.provider == provider)
        )
        record = result.scalar_one_or_none()
        if record is None:
            record = UserIntegration(user_id=user_id, organization_id=organization_id, provider=provider)
            session.add(record)
        record.encrypted_access_token = encrypt_token(tokens["access_token"])
        if tokens.get("refresh_token"):
            record.encrypted_refresh_token = encrypt_token(tokens["refresh_token"])
        record.token_expires_at = expires_at
        record.scopes = tokens.get("scope", "").split() if tokens.get("scope") else record.scopes
        record.status = "connected"
        record.enabled = True
        await session.flush()
        logger.info("User connected integration", extra={"user_id": user_id, "provider": provider})
        return _result_page(f"{provider.capitalize()} account connected. You can close this window and return to the app.")

    # kind == "org": admin-level org integration — see catalogue.py for the read side.
    from app.models import OrganizationIntegration
    result = await session.execute(
        select(OrganizationIntegration).where(
            OrganizationIntegration.organization_id == organization_id, OrganizationIntegration.provider == provider,
        )
    )
    org_record = result.scalar_one_or_none()
    if org_record is None:
        org_record = OrganizationIntegration(organization_id=organization_id, provider=provider, connected_by=user_id)
        session.add(org_record)
    org_record.encrypted_access_token = encrypt_token(tokens["access_token"])
    if tokens.get("refresh_token"):
        org_record.encrypted_refresh_token = encrypt_token(tokens["refresh_token"])
    org_record.token_expires_at = expires_at
    org_record.connected = True
    org_record.enabled = True
    org_record.connected_by = user_id
    await session.flush()
    logger.info("Org connected integration", extra={"organization_id": organization_id, "provider": provider})
    return _result_page(f"{provider.capitalize()} account connected for your organization. You can close this window.")


def _result_page(message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><body style=\"font-family:sans-serif;padding:2rem;text-align:center;\">"
        f"<p>{message}</p></body></html>"
    )


async def list_my_integrations(*, session: AsyncSession, user_id: str) -> list[UserIntegration]:
    result = await session.execute(select(UserIntegration).where(UserIntegration.user_id == user_id))
    return list(result.scalars().all())


async def disconnect_my_integration(*, session: AsyncSession, user_id: str, provider: str) -> None:
    result = await session.execute(
        select(UserIntegration).where(UserIntegration.user_id == user_id, UserIntegration.provider == provider)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found.")
    await session.delete(record)
    await session.flush()
