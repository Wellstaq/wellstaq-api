"""Service tests for app/services/platform_admin/platform_event_service.py."""

from __future__ import annotations

from datetime import date, time

import pytest
from fastapi import HTTPException

from app.services.platform_admin import platform_event_service
from tests.conftest import make_admin_user, make_org_with_owner


def _body(**overrides) -> dict:
    defaults = dict(
        title="Sleep better in 21 days", description=None, image_url=None,
        start_date=date(2026, 11, 1), end_date=None, time=time(17, 30), timezone="Africa/Lagos",
        audience="everyone", organization_ids=[],
    )
    return {**defaults, **overrides}


async def _admin(db_session):
    admin = make_admin_user()
    db_session.add(admin)
    await db_session.flush()
    return admin


@pytest.mark.asyncio
async def test_create_platform_event(db_session):
    admin = await _admin(db_session)
    result = await platform_event_service.create_platform_event(session=db_session, created_by=admin.id, body=_body())
    assert result["title"] == "Sleep better in 21 days"
    assert result["status"] == "scheduled"
    assert result["created_by"]["id"] == admin.id


@pytest.mark.asyncio
async def test_create_requires_organizations_for_selected_organizations_audience(db_session):
    admin = await _admin(db_session)
    with pytest.raises(HTTPException) as exc:
        await platform_event_service.create_platform_event(session=db_session, created_by=admin.id, body=_body(audience="selected_organizations", organization_ids=[]))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_with_selected_organizations(db_session):
    admin = await _admin(db_session)
    org, _owner = await make_org_with_owner(db_session)
    result = await platform_event_service.create_platform_event(
        session=db_session, created_by=admin.id, body=_body(audience="selected_organizations", organization_ids=[org.id]),
    )
    assert result["audience"] == "selected_organizations"
    assert result["organizations"] == [{"id": org.id, "name": org.business_name}]


@pytest.mark.asyncio
async def test_create_rejects_unknown_organization_id(db_session):
    admin = await _admin(db_session)
    with pytest.raises(HTTPException) as exc:
        await platform_event_service.create_platform_event(
            session=db_session, created_by=admin.id, body=_body(audience="selected_organizations", organization_ids=["does-not-exist"]),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_end_before_start(db_session):
    admin = await _admin(db_session)
    with pytest.raises(HTTPException) as exc:
        await platform_event_service.create_platform_event(
            session=db_session, created_by=admin.id, body=_body(start_date=date(2026, 11, 10), end_date=date(2026, 11, 1)),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_update_platform_event_title(db_session):
    admin = await _admin(db_session)
    created = await platform_event_service.create_platform_event(session=db_session, created_by=admin.id, body=_body())
    updated = await platform_event_service.update_platform_event(session=db_session, event_id=created["id"], body={"title": "New title"})
    assert updated["title"] == "New title"


@pytest.mark.asyncio
async def test_cancel_platform_event_sets_status(db_session):
    admin = await _admin(db_session)
    created = await platform_event_service.create_platform_event(session=db_session, created_by=admin.id, body=_body())
    cancelled = await platform_event_service.cancel_platform_event(session=db_session, event_id=created["id"])
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio
async def test_delete_platform_event_removes_it(db_session):
    admin = await _admin(db_session)
    created = await platform_event_service.create_platform_event(session=db_session, created_by=admin.id, body=_body())
    await platform_event_service.delete_platform_event(session=db_session, event_id=created["id"])
    with pytest.raises(HTTPException) as exc:
        await platform_event_service.get_platform_event(session=db_session, event_id=created["id"])
    assert exc.value.status_code == 404
