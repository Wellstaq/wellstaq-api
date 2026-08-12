from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SupportTicket


async def create_support_ticket(db: AsyncSession, *, user_id: str, organization_id: str,
                                 name: str, email: str, subject: str, message: str) -> SupportTicket:
    ticket = SupportTicket(
        user_id=user_id,
        organization_id=organization_id,
        name=name.strip(),
        email=email.lower(),
        subject=subject.strip(),
        message=message.strip(),
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def list_support_tickets_admin(db: AsyncSession, *, status_filter: str | None,
                                      offset: int, limit: int) -> tuple[list[SupportTicket], int]:
    limit = min(max(limit, 1), 100)
    filters = [SupportTicket.status == status_filter] if status_filter else []
    total = (await db.execute(select(func.count()).select_from(SupportTicket).where(*filters))).scalar_one()
    rows = (await db.execute(
        select(SupportTicket).where(*filters)
        .order_by(SupportTicket.created_at.desc())
        .offset(max(offset, 0)).limit(limit)
    )).scalars().all()
    return rows, total


async def update_support_ticket_status(db: AsyncSession, ticket_id: str, new_status: str) -> SupportTicket:
    ticket = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support ticket not found.")
    ticket.status = new_status
    await db.flush()
    return ticket