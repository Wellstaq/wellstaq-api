import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.config import settings
from app.core.guards import request_guard
from app.models import Branch, Department, DepartmentMember, OrganizationIntegration, Role, SupportTicket, User

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

class TicketStatusRequest(BaseModel):
    status: str = Field(pattern="^(open|in_progress|resolved|closed)$")

def require_app_admin_key(x_app_admin_key: str | None = Header(default=None)) -> None:
    if not settings.APP_ADMIN_API_KEY or not x_app_admin_key or not secrets.compare_digest(x_app_admin_key, settings.APP_ADMIN_API_KEY):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Valid app-admin key required.")

@router.get("/v1/app-admin/support/tickets", dependencies=[Depends(require_app_admin_key)])
async def list_support_tickets(status_filter: str | None = None, offset: int = 0, limit: int = 50,
                               db: AsyncSession = Depends(get_db)):
    """Temporary key-protected endpoint until the app-admin authentication flow exists."""
    limit = min(max(limit, 1), 100)
    filters = [SupportTicket.status == status_filter] if status_filter else []
    total = (await db.execute(select(func.count()).select_from(SupportTicket).where(*filters))).scalar_one()
    rows = (await db.execute(select(SupportTicket).where(*filters)
                            .order_by(SupportTicket.created_at.desc()).offset(max(offset, 0)).limit(limit))).scalars().all()
    return {"items": [{"id": row.id, "user_id": row.user_id, "organization_id": row.organization_id,
                       "name": row.name, "email": row.email, "subject": row.subject,
                       "message": row.message, "status": row.status,
                       "created_at": row.created_at.isoformat()} for row in rows], "total": total}

@router.patch("/v1/app-admin/support/tickets/{ticket_id}", dependencies=[Depends(require_app_admin_key)])
async def update_support_ticket(ticket_id: str, body: TicketStatusRequest, db: AsyncSession = Depends(get_db)):
    ticket = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(404, "Support ticket not found.")
    ticket.status = body.status
    await db.commit()
    return {"id": ticket.id, "status": ticket.status}

@router.get("/v1/users/search")
async def search_users(q: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    query = q.strip()
    if len(query) < 2:
        return []
    # Prefix matches rank above substring matches, while still tolerating partial names.
    pattern = f"%{query}%"
    prefix = f"{query}%"
    users = (await db.execute(select(User).where(
        User.organization_id == current_user.organization_id,
        or_(User.first_name.ilike(pattern), User.last_name.ilike(pattern), User.email.ilike(pattern)),
    ).order_by(
        func.lower(User.first_name).like(prefix.lower()).desc(),
        func.lower(User.last_name).like(prefix.lower()).desc(),
        func.lower(User.email).like(prefix.lower()).desc(),
        User.first_name, User.last_name,
    ).limit(10))).scalars().all()
    return [{"id": user.id, "name": f"{user.first_name} {user.last_name}".strip(),
             "email": user.email, "avatar": user.avatar_url} for user in users]

@router.get("/v1/users/{user_id}/profile")
async def get_user_profile(user_id: str, db: AsyncSession = Depends(get_db),
                           current_user: CurrentUser = Depends(get_current_user)):
    row = (await db.execute(
        select(User, Branch.name, Department.id, Department.name, Role.name)
        .outerjoin(Branch, Branch.id == User.branch_id)
        .outerjoin(DepartmentMember, DepartmentMember.user_id == User.id)
        .outerjoin(Department, Department.id == DepartmentMember.department_id)
        .outerjoin(Role, Role.id == User.role_id)
        .where(User.id == user_id, User.organization_id == current_user.organization_id)
    )).first()
    if row is None:
        raise HTTPException(404, "User profile not found.")
    user, branch_name, department_id, department_name, role_name = row
    return {"id": user.id, "first_name": user.first_name, "last_name": user.last_name,
            "name": f"{user.first_name} {user.last_name}".strip(), "email": user.email,
            "phone_number": user.phone_number, "profile_image": user.avatar_url,
            "country": user.country, "state": user.state, "status": user.status,
            "branch_id": user.branch_id, "branch_name": branch_name,
            "department_id": department_id, "department_name": department_name,
            "role": role_name, "joined_at": user.created_at.isoformat()}

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
