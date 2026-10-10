"""ORM and integrity tests for app/models/platform_event.py."""

from datetime import date, time

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import PlatformEvent, PlatformEventOrganization, PlatformEventParticipant
from tests.conftest import make_admin_user, make_org_with_owner


def _event(**overrides) -> PlatformEvent:
    defaults = dict(title="Sleep better in 21 days", start_date=date(2026, 11, 1), time=time(17, 30), audience="everyone")
    return PlatformEvent(**{**defaults, **overrides})


@pytest.mark.asyncio
async def test_platform_event_persists_defaults(db_session):
    event = _event()
    db_session.add(event)
    await db_session.flush()
    await db_session.refresh(event)

    assert event.status == "scheduled"
    assert event.timezone == "Africa/Lagos"
    assert event.recurrence_rule is None


@pytest.mark.asyncio
async def test_platform_event_rejects_empty_title(db_session):
    db_session.add(_event(title="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_platform_event_rejects_end_before_start(db_session):
    db_session.add(_event(start_date=date(2026, 11, 10), end_date=date(2026, 11, 1)))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_platform_event_rejects_invalid_audience(db_session):
    db_session.add(_event(audience="everyone_except_rivals"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_platform_event_organization_unique_pair(db_session):
    org, _owner = await make_org_with_owner(db_session)
    event = _event(audience="selected_organizations")
    db_session.add(event)
    await db_session.flush()

    db_session.add(PlatformEventOrganization(event_id=event.id, organization_id=org.id))
    await db_session.flush()
    db_session.add(PlatformEventOrganization(event_id=event.id, organization_id=org.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_platform_event_participant_unique_pair(db_session):
    org, owner = await make_org_with_owner(db_session)
    event = _event()
    db_session.add(event)
    await db_session.flush()

    db_session.add(PlatformEventParticipant(event_id=event.id, user_id=owner.id))
    await db_session.flush()
    db_session.add(PlatformEventParticipant(event_id=event.id, user_id=owner.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_platform_event_created_by_survives_admin_deletion(db_session):
    admin = make_admin_user()
    db_session.add(admin)
    await db_session.flush()

    event = _event(created_by=admin.id)
    db_session.add(event)
    await db_session.flush()

    await db_session.delete(admin)
    await db_session.flush()
    await db_session.refresh(event)

    assert event.created_by is None
