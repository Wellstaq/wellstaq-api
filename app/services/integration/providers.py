"""
app/services/integration/providers.py

Registry of OAuth providers this backend knows how to talk to. Adding a
new one (Slack, Zoom, Microsoft) means adding ONE entry here plus the
3 settings it points at (app/core/config.py) — nothing else in
oauth_client.py, connection_service.py, or the router needs to change,
since they all look providers up by name through this registry.

Scopes/endpoints come from each provider's own OAuth docs:
  Google:   https://developers.google.com/identity/protocols/oauth2/web-server
  Slack:    https://api.slack.com/authentication/oauth-v2
  Zoom:     https://developers.zoom.us/docs/integrations/oauth/
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, status


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    auth_endpoint: str
    token_endpoint: str
    scopes: list[str]
    # Names of the three settings.* attributes holding this provider's
    # credentials — see app/core/config.py's "<PROVIDER>_INTEGRATION_*" group.
    client_id_setting: str
    client_secret_setting: str
    redirect_uri_setting: str
    # Provider-specific authorize-URL params beyond the standard ones
    # (client_id/redirect_uri/response_type/scope/state), e.g. Google's
    # offline-access flags. Most new providers won't need any.
    extra_auth_params: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[str, OAuthProvider] = {
    "google": OAuthProvider(
        name="google",
        auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        # calendar.events: create/update events on the user's calendar only
        # (never calendar.readonly — Wellstaq doesn't read the rest of
        # someone's calendar). fitness.activity.read: step/distance/calories
        # aggregates for automatic challenge tracking, read-only.
        scopes=[
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/fitness.activity.read",
        ],
        client_id_setting="GOOGLE_INTEGRATION_CLIENT_ID",
        client_secret_setting="GOOGLE_INTEGRATION_CLIENT_SECRET",
        redirect_uri_setting="GOOGLE_INTEGRATION_REDIRECT_URI",
        extra_auth_params={
            "access_type": "offline",  # required to get a refresh_token back
            "prompt": "consent",  # force refresh_token on reconnect too, not just first consent
            "include_granted_scopes": "true",
        },
    ),
}

# Maps a catalogue.py feature id (admin-facing, e.g. "google_calendar") to
# the underlying OAuth provider that powers it (this registry's keys).
# A catalogue entry with no mapping here still shows up in the list but
# its /connect route 503s with "not configured yet" — that's the normal
# state for a provider nobody has built OAuth support for.
CATALOGUE_OAUTH_PROVIDER: dict[str, str] = {
    "google_calendar": "google",
}


def get_provider(provider_name: str) -> OAuthProvider:
    provider = PROVIDERS.get(provider_name)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{provider_name}' is not a supported OAuth integration.")
    return provider
