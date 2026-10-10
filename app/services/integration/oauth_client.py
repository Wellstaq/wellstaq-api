"""
app/services/integration/oauth_client.py

Provider-agnostic OAuth2 authorization-code flow. Raw httpx calls rather
than a provider SDK — consistent with the rest of the codebase (Paystack,
Brevo etc. are all called directly via httpx too), and it means adding a
new provider never needs a new dependency.

Every function takes a provider name and looks up its endpoints/scopes/
settings from the providers.py registry — see that file to add a new
provider. Until a provider's 3 settings are filled in, every function
here raises IntegrationNotConfigured, which the router turns into a 503.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.services.integration.crypto import IntegrationNotConfigured
from app.services.integration.providers import OAuthProvider, get_provider


def _credentials(provider: OAuthProvider) -> tuple[str, str, str]:
    client_id = getattr(settings, provider.client_id_setting)
    client_secret = getattr(settings, provider.client_secret_setting)
    redirect_uri = getattr(settings, provider.redirect_uri_setting)
    if not (client_id and client_secret and redirect_uri):
        raise IntegrationNotConfigured(
            f"'{provider.name}' integration is not configured yet — set {provider.client_id_setting}, "
            f"{provider.client_secret_setting}, and {provider.redirect_uri_setting} on the backend."
        )
    return client_id, client_secret, redirect_uri


def build_authorization_url(provider_name: str, state: str) -> str:
    provider = get_provider(provider_name)
    client_id, _client_secret, redirect_uri = _credentials(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        **provider.extra_auth_params,
    }
    return f"{provider.auth_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(provider_name: str, code: str) -> dict:
    provider = get_provider(provider_name)
    client_id, client_secret, redirect_uri = _credentials(provider)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            provider.token_endpoint,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{provider.name} rejected the authorization code: {response.text}",
        )
    return response.json()


async def refresh_access_token(provider_name: str, refresh_token: str) -> dict:
    provider = get_provider(provider_name)
    client_id, client_secret, _redirect_uri = _credentials(provider)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            provider.token_endpoint,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code != 200:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{provider.name} refresh token is no longer valid — please reconnect this integration.",
        )
    return response.json()
