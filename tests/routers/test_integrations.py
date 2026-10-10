"""HTTP route tests for app/routers/integrations.py."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationIntegration
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"integration-router-{user.id}")
    return org, headers


class TestIntegrationRoutes:
    @pytest.mark.asyncio
    async def test_list_returns_catalogue(self, client, db_session):
        org, headers = await _context(db_session)
        response = await client.get(f"/organizations/{org.id}/integrations", headers=headers)
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == {
            "google_calendar", "slack", "zoom", "microsoft_outlook"
        }

    @pytest.mark.asyncio
    async def test_connect_reports_missing_oauth_configuration(self, client, db_session):
        org, headers = await _context(db_session)
        response = await client.post(
            f"/organizations/{org.id}/integrations/google_calendar/connect", headers=headers
        )
        assert response.status_code == 503
        assert "GOOGLE_INTEGRATION_CLIENT_ID" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_toggle_connected_integration_and_disconnect(self, client, db_session):
        org, headers = await _context(db_session)
        row = OrganizationIntegration(
            organization_id=org.id, provider="google_calendar", connected=True, enabled=False
        )
        db_session.add(row)
        await db_session.flush()

        toggled = await client.patch(
            f"/organizations/{org.id}/integrations/google_calendar",
            headers=headers,
            json={"enabled": True},
        )
        assert toggled.status_code == 200
        assert toggled.json()["enabled"] is True

        disconnected = await client.delete(
            f"/organizations/{org.id}/integrations/google_calendar", headers=headers
        )
        assert disconnected.status_code == 204
        assert (
            await db_session.execute(select(OrganizationIntegration).where(OrganizationIntegration.id == row.id))
        ).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_toggle_requires_connected_integration(self, client, db_session):
        org, headers = await _context(db_session)
        response = await client.patch(
            f"/organizations/{org.id}/integrations/slack", headers=headers, json={"enabled": True}
        )
        assert response.status_code == 409
