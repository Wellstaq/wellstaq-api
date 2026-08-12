import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.core.guards import request_guard
from app.services import support_ticket as support_ticket_service
from app.schemas.support_ticket import (
    TicketCreateRequest, TicketCreateResponse, TicketStatusRequest, TicketStatusResponse,
    TicketAdminListResponse, TicketAdminItem,
)

router = APIRouter(tags=["support tickets"])


def require_app_admin_key(x_app_admin_key: str | None = Header(default=None)) -> None:
    if not settings.APP_ADMIN_API_KEY or not x_app_admin_key or not secrets.compare_digest(x_app_admin_key, settings.APP_ADMIN_API_KEY):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Valid app-admin key required.")


@router.get("/app-admin/support/tickets", response_model=TicketAdminListResponse,
           dependencies=[Depends(require_app_admin_key)])
async def list_support_tickets(status_filter: str | None = None, offset: int = 0, limit: int = 50,
                               db: AsyncSession = Depends(get_db)):
    """Temporary key-protected endpoint until the app-admin authentication flow exists."""
    rows, total = await support_ticket_service.list_support_tickets_admin(
        db, status_filter=status_filter, offset=offset, limit=limit)
    return {
        "items": [
            TicketAdminItem(
                id=row.id, user_id=row.user_id, organization_id=row.organization_id,
                name=row.name, email=row.email, subject=row.subject, message=row.message,
                status=row.status, created_at=row.created_at.isoformat(),
            ) for row in rows
        ],
        "total": total,
    }


@router.patch("/app-admin/support/tickets/{ticket_id}", response_model=TicketStatusResponse,
             dependencies=[Depends(require_app_admin_key)])
async def update_support_ticket(ticket_id: str, body: TicketStatusRequest, db: AsyncSession = Depends(get_db)):
    ticket = await support_ticket_service.update_support_ticket_status(db, ticket_id, body.status)
    await db.commit()
    return {"id": ticket.id, "status": ticket.status}


@router.post("/support/tickets", response_model=TicketCreateResponse, status_code=201)
async def create_ticket(body: TicketCreateRequest, db: AsyncSession = Depends(get_db),
                        current_user: CurrentUser = Depends(get_current_user)):
    async with request_guard(db, "/support/tickets"):
        ticket = await support_ticket_service.create_support_ticket(
            db, user_id=current_user.user_id, organization_id=current_user.organization_id,
            name=body.name, email=str(body.email), subject=body.subject, message=body.message)
        await db.commit()
        return {"accepted": True, "ticket_id": ticket.id, "status": ticket.status}