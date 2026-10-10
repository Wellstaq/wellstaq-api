"""Service tests for app/services/platform_event_service.py — the
employee/individual-facing read + join side of featured events."""

from __future__ import annotations

from datetime import date, time

import pytest
from fastapi import HTTPException

from app.models import PlatformEvent
from app.services import platform_event_service
from tests.conftest import make_org_with_owner


def _event(**overrides) -> PlatformEvent:
    defaults = dict(title="Sleep better in 21 days", start_date=date(2026, 11, 1), time=time(17, 30), audience="everyone")
    return PlatformEvent(**{**defaults, **overrides})


@pytest.mark.asyncio
async def test_everyone_audience_is_visible_to_any_org(db_session):
    org, _owner = await make_org_with_owner(db_session)
    db_session.add(_event(audience="everyone"))
    await db_session.flush()

    rows, total = await platform_event_service.list_platform_events_for_user(session=db_session, organization_id=org.id, offset=0, limit=20)
    assert total == 1


@pytest.mark.asyncio
async def test_employees_audience_is_visible_to_any_org(db_session):
    org, _owner = await make_org_with_owner(db_session)
    db_session.add(_event(audience="employees"))
    await db_session.flush()

    rows, total = await platform_event_service.list_platform_events_for_user(session=db_session, organization_id=org.id, offset=0, limit=20)
    assert total == 1


@pytest.mark.asyncio
async def test_selected_organizations_audience_hidden_from_unlisted_org(db_session):
    from app.models import PlatformEventOrganization

    visible_org, _ = await make_org_with_owner(db_session, org_kwargs={"business_name": "Visible Co", "slug": "visible-co"}, user_kwargs={"email": "visible-owner@example.com"})
    hidden_org, _ = await make_org_with_owner(db_session, org_kwargs={"business_name": "Hidden Co", "slug": "hidden-co"}, user_kwargs={"email": "hidden-owner@example.com"})
    event = _event(audience="selected_organizations")
    db_session.add(event)
    await db_session.flush()
    db_session.add(PlatformEventOrganization(event_id=event.id, organization_id=visible_org.id))
    await db_session.flush()

    visible_rows, visible_total = await platform_event_service.list_platform_events_for_user(session=db_session, organization_id=visible_org.id, offset=0, limit=20)
    hidden_rows, hidden_total = await platform_event_service.list_platform_events_for_user(session=db_session, organization_id=hidden_org.id, offset=0, limit=20)
    assert visible_total == 1
    assert hidden_total == 0


@pytest.mark.asyncio
async def test_cancelled_events_are_excluded(db_session):
    org, _owner = await make_org_with_owner(db_session)
    db_session.add(_event(audience="everyone", status="cancelled"))
    await db_session.flush()

    rows, total = await platform_event_service.list_platform_events_for_user(session=db_session, organization_id=org.id, offset=0, limit=20)
    assert total == 0


@pytest.mark.asyncio
async def test_join_and_leave_platform_event(db_session):
    org, owner = await make_org_with_owner(db_session)
    event = _event(audience="everyone")
    db_session.add(event)
    await db_session.flush()

    await platform_event_service.join_platform_event(session=db_session, event_id=event.id, user_id=owner.id, organization_id=org.id)
    # Idempotent — joining again is a silent no-op, not a conflict.
    await platform_event_service.join_platform_event(session=db_session, event_id=event.id, user_id=owner.id, organization_id=org.id)

    await platform_event_service.leave_platform_event(session=db_session, event_id=event.id, user_id=owner.id)
    with pytest.raises(HTTPException) as exc:
        await platform_event_service.leave_platform_event(session=db_session, event_id=event.id, user_id=owner.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_join_blocked_for_org_not_in_selected_organizations(db_session):
    from app.models import PlatformEventOrganization

    allowed_org, _ = await make_org_with_owner(db_session, org_kwargs={"business_name": "Allowed Co", "slug": "allowed-co"}, user_kwargs={"email": "allowed-owner@example.com"})
    other_org, other_owner = await make_org_with_owner(db_session, org_kwargs={"business_name": "Other Co", "slug": "other-co"}, user_kwargs={"email": "other-owner@example.com"})
    event = _event(audience="selected_organizations")
    db_session.add(event)
    await db_session.flush()
    db_session.add(PlatformEventOrganization(event_id=event.id, organization_id=allowed_org.id))
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await platform_event_service.join_platform_event(session=db_session, event_id=event.id, user_id=other_owner.id, organization_id=other_org.id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_join_cancelled_event_conflicts(db_session):
    org, owner = await make_org_with_owner(db_session)
    event = _event(audience="everyone", status="cancelled")
    db_session.add(event)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await platform_event_service.join_platform_event(session=db_session, event_id=event.id, user_id=owner.id, organization_id=org.id)
    assert exc.value.status_code == 409
