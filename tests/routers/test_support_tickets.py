"""HTTP route tests for app/routers/support_tickets.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import authenticate_admin, authenticate_user, make_admin_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"ticket-router-{user.id}")
    return user, headers


async def _admin_context(db_session: AsyncSession, **admin_overrides):
    admin = make_admin_user(**admin_overrides)
    db_session.add(admin)
    await db_session.flush()
    headers, _ = await authenticate_admin(db_session, admin)
    return admin, headers


class TestSupportTicketRoutes:
    @pytest.mark.asyncio
    async def test_create_list_and_update_ticket(self, client, db_session):
        user, headers = await _context(db_session)
        _, admin_headers = await _admin_context(db_session)
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
    async def test_admin_routes_require_authentication(self, client):
        response = await client.get("/app-admin/support/tickets")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_routes_reject_org_user_token(self, client, db_session):
        """An org user's access token must never work on /app-admin/* —
        admins are a wholly separate identity (see app/models/admin_user.py)."""
        _, headers = await _context(db_session)
        response = await client.get("/app-admin/support/tickets", headers=headers)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_support_role_can_manage_tickets_but_not_suspend_organizations(self, client, db_session):
        """support has organizations.read (can list) but not
        organizations.manage (can't suspend) — see ADMIN_ROLE_PERMISSIONS."""
        _, admin_headers = await _admin_context(db_session, email="support@wellstaq.test", role="support")
        listed = await client.get("/app-admin/support/tickets", headers=admin_headers)
        assert listed.status_code == 200

        readable = await client.get("/app-admin/organizations", headers=admin_headers)
        assert readable.status_code == 200

        blocked = await client.post(
            "/app-admin/organizations/nonexistent-id/suspend", headers=admin_headers, json={"reason": "test"},
        )
        assert blocked.status_code == 403

    @pytest.mark.asyncio
    async def test_create_validates_message_length(self, client, db_session):
        user, headers = await _context(db_session)
        response = await client.post(
            "/support/tickets",
            headers=headers,
            json={"name": "Ada User", "email": user.email, "subject": "Help", "message": "short"},
        )
        assert response.status_code == 422
