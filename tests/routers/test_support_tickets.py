"""HTTP route tests for app/routers/support_tickets.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"ticket-router-{user.id}")
    return user, headers


class TestSupportTicketRoutes:
    @pytest.mark.asyncio
    async def test_create_list_and_update_ticket(self, client, db_session, monkeypatch):
        user, headers = await _context(db_session)
        monkeypatch.setattr(settings, "APP_ADMIN_API_KEY", "test-admin-secret")
        created = await client.post(
            "/support/tickets",
            headers=headers,
            json={
                "name": f"{user.first_name} {user.last_name}",
                "email": user.email,
                "subject": "Billing help",
                "message": "Please help me understand this payment.",
            },
        )
        assert created.status_code == 201
        ticket_id = created.json()["ticket_id"]
        admin_headers = {"X-App-Admin-Key": "test-admin-secret"}

        listed = await client.get("/app-admin/support/tickets", headers=admin_headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == ticket_id

        updated = await client.patch(
            f"/app-admin/support/tickets/{ticket_id}",
            headers=admin_headers,
            json={"status": "resolved"},
        )
        assert updated.status_code == 200
        assert updated.json() == {"id": ticket_id, "status": "resolved"}

    @pytest.mark.asyncio
    async def test_admin_routes_require_valid_key(self, client, monkeypatch):
        monkeypatch.setattr(settings, "APP_ADMIN_API_KEY", "test-admin-secret")
        response = await client.get("/app-admin/support/tickets")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_validates_message_length(self, client, db_session):
        user, headers = await _context(db_session)
        response = await client.post(
            "/support/tickets",
            headers=headers,
            json={"name": "Ada User", "email": user.email, "subject": "Help", "message": "short"},
        )
        assert response.status_code == 422
