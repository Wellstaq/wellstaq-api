"""ORM tests for app/models/support_ticket.py."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SupportTicket
from tests.conftest import make_org_with_owner


def make_ticket(user_id: str, organization_id: str, **overrides) -> SupportTicket:
    values = {
        "user_id": user_id,
        "organization_id": organization_id,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "subject": "billing",
        "message": "Please help with a payment.",
    }
    return SupportTicket(**{**values, **overrides})


@pytest.mark.asyncio
async def test_support_ticket_persists_with_open_status(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    ticket = make_ticket(owner.id, org.id)
    db_session.add(ticket)
    await db_session.flush()

    assert ticket.id is not None
    assert ticket.status == "open"
    assert ticket.created_at is not None
    assert ticket.updated_at is not None


@pytest.mark.asyncio
async def test_support_ticket_status_can_be_updated(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    ticket = make_ticket(owner.id, org.id)
    db_session.add(ticket)
    await db_session.flush()

    ticket.status = "resolved"
    await db_session.flush()

    assert ticket.status == "resolved"


@pytest.mark.asyncio
async def test_support_ticket_requires_message(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    ticket = make_ticket(owner.id, org.id)
    ticket.message = None
    db_session.add(ticket)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_support_ticket_requires_existing_user_and_org(db_session: AsyncSession):
    ticket = make_ticket(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    )
    db_session.add(ticket)

    with pytest.raises(IntegrityError):
        await db_session.flush()
