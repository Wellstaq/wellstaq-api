from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.services import integration as integrations_service
from app.schemas.integration import (
    ConnectIntegrationResponse,
    IntegrationListResponse,
    MyIntegrationItem,
    MyIntegrationListResponse,
    SyncResultResponse,
    ToggleIntegrationRequest,
    ToggleIntegrationResponse,
)

router = APIRouter(tags=["integrations"])


@router.get("/organizations/{org_id}/integrations", response_model=IntegrationListResponse)
async def list_integrations(org_id: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/integrations"):
        items = await integrations_service.list_org_integrations(db, org_id)
        return {"items": items}


@router.post("/organizations/{org_id}/integrations/{provider}/connect", response_model=ConnectIntegrationResponse)
async def connect_integration(org_id: str, provider: str, current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    name, _ = integrations_service.get_catalogue_entry(provider)
    oauth_provider = integrations_service.CATALOGUE_OAUTH_PROVIDER.get(provider)
    if oauth_provider is None:
        raise HTTPException(503, f"{name} OAuth is not configured yet. Add its client ID, client secret, and callback URL on the backend.")

    from app.auth.jwt import create_oauth_state_token
    from app.services.integration.crypto import IntegrationNotConfigured
    from app.services.integration.oauth_client import build_authorization_url

    # kind="org" — this is the admin connecting the ORGANIZATION's account
    # (e.g. a shared company calendar), stored on OrganizationIntegration;
    # distinct from an employee's personal /me/integrations/{provider}/connect
    # below (kind="user", UserIntegration).
    state = create_oauth_state_token(user_id=current_user.user_id, organization_id=org_id, provider=oauth_provider, kind="org")
    try:
        url = build_authorization_url(oauth_provider, state)
    except IntegrationNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    return ConnectIntegrationResponse(authorization_url=url)


@router.patch("/organizations/{org_id}/integrations/{provider}", response_model=ToggleIntegrationResponse)
async def toggle_integration(org_id: str, provider: str, body: ToggleIntegrationRequest,
                             db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/integrations/{provider}"):
        row = await integrations_service.toggle_org_integration(db, org_id, provider, body.enabled)
        await db.commit()
        return {"enabled": row.enabled}


@router.delete("/organizations/{org_id}/integrations/{provider}", status_code=204)
async def disconnect_integration(org_id: str, provider: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/integrations/{provider}"):
        await integrations_service.disconnect_org_integration(db, org_id, provider)
        await db.commit()
        return Response(status_code=204)


# ── OAuth callback — hit directly by the provider's browser redirect,   ─────
# not called as a JSON API. Shared by both the org-level connect above and
# the personal /me/integrations/{provider}/connect below (kind in the
# state param says which). No org_id prefix: the provider doesn't know
# it, the org is recovered from the signed state. {provider} in the path
# is what you register as the redirect URI in each provider's own OAuth
# console, e.g. .../integrations/google/callback, .../integrations/slack/callback.
@router.get("/integrations/{provider}/callback", response_class=HTMLResponse)
async def oauth_callback_route(
    provider: str,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    async with request_guard(db, "/integrations/{provider}/callback"):
        page = await integrations_service.handle_oauth_callback(session=db, provider=provider, code=code, state=state, error=error)
        await db.commit()
        return page


# ── Personal (user-level) integrations — the employee app's own Google ─────
# connect, e.g. for Google Fit step data feeding automatic challenge
# tracking. Distinct from the org-scoped catalogue above.

@router.post("/me/integrations/{provider}/connect", response_model=ConnectIntegrationResponse)
async def connect_my_integration_route(
    provider: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    url = integrations_service.start_user_connect(
        user_id=current_user.user_id, organization_id=current_user.organization_id, provider=provider,
    )
    return ConnectIntegrationResponse(authorization_url=url)


@router.get("/me/integrations", response_model=MyIntegrationListResponse)
async def list_my_integrations_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    async with request_guard(db, "/me/integrations"):
        rows = await integrations_service.list_my_integrations(session=db, user_id=current_user.user_id)
        return MyIntegrationListResponse(items=[
            MyIntegrationItem(provider=r.provider, status=r.status, enabled=r.enabled, last_synced_at=r.last_synced_at)
            for r in rows
        ])


@router.delete("/me/integrations/{provider}", status_code=204)
async def disconnect_my_integration_route(
    provider: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    async with request_guard(db, "/me/integrations/{provider}"):
        await integrations_service.disconnect_my_integration(session=db, user_id=current_user.user_id, provider=provider)
        await db.commit()
        return Response(status_code=204)


@router.post("/me/integrations/google/sync", response_model=SyncResultResponse)
async def sync_my_google_fit_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    async with request_guard(db, "/me/integrations/google/sync"):
        entries, challenge_ids = await integrations_service.sync_google_fit_steps(
            session=db, organization_id=current_user.organization_id,
            branch_id=current_user.branch_id, user_id=current_user.user_id,
        )
        await db.commit()
        return SyncResultResponse(synced_entries=len(entries), challenge_ids=challenge_ids)