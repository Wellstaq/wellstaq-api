from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import OrganizationIntegration, SupportTicket, User

router = APIRouter(tags=["support and integrations"])

CATALOGUE = {
    "google_calendar": ("Google Calendar", "Sync wellness events and challenges to Google Calendar."),
    "slack": ("Slack", "Send wellness challenge and activity notifications to Slack."),
    "zoom": ("Zoom", "Generate Zoom links for virtual wellness sessions."),
    "microsoft_outlook": ("Microsoft Outlook", "Sync events and reminders through Outlook."),
}

class TicketRequest(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    subject: str = Field(min_length=2, max_length=100)
    message: str = Field(min_length=10, max_length=5000)

class ToggleRequest(BaseModel):
    enabled: bool

@router.get("/v1/users/search")
async def search_users(q: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    query = q.strip()
    if len(query) < 2:
        return []
    pattern = f"%{query}%"
    users = (await db.execute(select(User).where(
        User.organization_id == current_user.organization_id,
        or_(User.first_name.ilike(pattern), User.last_name.ilike(pattern), User.email.ilike(pattern)),
    ).order_by(User.first_name, User.last_name).limit(10))).scalars().all()
    return [{"id": user.id, "name": f"{user.first_name} {user.last_name}".strip(),
             "email": user.email, "avatar": user.avatar_url} for user in users]

@router.post("/v1/support/tickets", status_code=201)
async def create_ticket(body: TicketRequest, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    async with request_guard(db, "/v1/support/tickets"):
        ticket = SupportTicket(user_id=current_user.user_id, organization_id=current_user.organization_id,
                               name=body.name.strip(), email=str(body.email).lower(), subject=body.subject.strip(), message=body.message.strip())
        db.add(ticket)
        await db.commit()
        return {"accepted": True, "ticket_id": ticket.id, "status": ticket.status}

@router.get("/organizations/{org_id}/integrations")
async def list_integrations(org_id: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/integrations"):
        rows = (await db.execute(select(OrganizationIntegration).where(OrganizationIntegration.organization_id == org_id))).scalars().all()
        states = {row.provider: row for row in rows}
        return {"items": [{"id": provider, "name": name, "description": description,
                           "status": "connected" if states.get(provider) and states[provider].connected else "disconnected",
                           "enabled": bool(states.get(provider) and states[provider].enabled)}
                          for provider, (name, description) in CATALOGUE.items()]}

@router.post("/organizations/{org_id}/integrations/{provider}/connect")
async def connect_integration(org_id: str, provider: str, current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    if provider not in CATALOGUE:
        raise HTTPException(404, "Integration provider not found.")
    raise HTTPException(503, f"{CATALOGUE[provider][0]} OAuth is not configured yet. Add its client ID, client secret, and callback URL on the backend.")

@router.patch("/organizations/{org_id}/integrations/{provider}")
async def toggle_integration(org_id: str, provider: str, body: ToggleRequest, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    row = (await db.execute(select(OrganizationIntegration).where(OrganizationIntegration.organization_id == org_id, OrganizationIntegration.provider == provider))).scalar_one_or_none()
    if row is None or not row.connected:
        raise HTTPException(409, "Connect this integration before enabling it.")
    row.enabled = body.enabled
    await db.commit()
    return {"enabled": row.enabled}

@router.delete("/organizations/{org_id}/integrations/{provider}", status_code=204)
async def disconnect_integration(org_id: str, provider: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    require_same_organization(org_id, current_user)
    row = (await db.execute(select(OrganizationIntegration).where(OrganizationIntegration.organization_id == org_id, OrganizationIntegration.provider == provider))).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return Response(status_code=204)
