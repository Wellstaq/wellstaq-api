"""HTTP route tests for app/routers/platform_events.py."""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformEvent
from tests.conftest import authenticate_user, make_org_with_owner


def _event(**overrides) -> PlatformEvent:
    defaults = dict(title="Sleep better in 21 days", start_date=date(2026, 11, 1), time=time(17, 30), audience="everyone")
    return PlatformEvent(**{**defaults, **overrides})


class TestPlatformEventRoutes:
    @pytest.mark.asyncio
    async def test_list_requires_authentication(self, client):
        response = await client.get("/platform-events")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_and_join_round_trip(self, client, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        headers, _ = await authenticate_user(db_session, owner)
        event = _event()
        db_session.add(event)
        await db_session.flush()

        listed = await client.get("/platform-events", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        joined = await client.post(f"/platform-events/{event.id}/join", headers=headers)
        assert joined.status_code == 201

        left = await client.request("DELETE", f"/platform-events/{event.id}/join", headers=headers)
        assert left.status_code == 200

    @pytest.mark.asyncio
    async def test_join_blocked_for_unlisted_org(self, client, db_session: AsyncSession):
        from app.models import PlatformEventOrganization

        allowed_org, _ = await make_org_with_owner(db_session, org_kwargs={"business_name": "Allowed Co", "slug": "allowed-co"}, user_kwargs={"email": "allowed-owner@example.com"})
        other_org, other_owner = await make_org_with_owner(db_session, org_kwargs={"business_name": "Other Co", "slug": "other-co"}, user_kwargs={"email": "other-owner@example.com"})
        headers, _ = await authenticate_user(db_session, other_owner)

        event = _event(audience="selected_organizations")
        db_session.add(event)
        await db_session.flush()
        db_session.add(PlatformEventOrganization(event_id=event.id, organization_id=allowed_org.id))
        await db_session.flush()

        response = await client.post(f"/platform-events/{event.id}/join", headers=headers)
        assert response.status_code == 403
