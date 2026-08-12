from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.services import integration as integrations_service
from app.schemas.integration import IntegrationListResponse, ToggleIntegrationRequest, ToggleIntegrationResponse

router = APIRouter(tags=["integrations"])


@router.get("/organizations/{org_id}/integrations", response_model=IntegrationListResponse)
async def list_integrations(org_id: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/integrations"):
        items = await integrations_service.list_org_integrations(db, org_id)
        return {"items": items}


@router.post("/organizations/{org_id}/integrations/{provider}/connect")
async def connect_integration(org_id: str, provider: str, current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    name, _ = integrations_service.get_catalogue_entry(provider)
    raise HTTPException(503, f"{name} OAuth is not configured yet. Add its client ID, client secret, and callback URL on the backend.")


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