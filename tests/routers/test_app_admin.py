"""
HTTP route tests for app/routers/app_admin.py.

Exercised through FastAPI's ASGI client with the real database, admin
auth, permission, service, and response-schema layers — same approach
as tests/routers/test_challenges.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from tests.conftest import authenticate_admin, authenticate_user, make_admin_user, make_org_with_owner


async def _admin_context(db_session: AsyncSession, **overrides):
    overrides.setdefault("hashed_password", hash_password("correct-horse-battery-staple"))
    admin = make_admin_user(**overrides)
    db_session.add(admin)
    await db_session.flush()
    headers, _ = await authenticate_admin(db_session, admin)
    return admin, headers


class TestAdminAuthRoutes:
    @pytest.mark.asyncio
    async def test_login_then_me_round_trip(self, client, db_session: AsyncSession):
        admin = make_admin_user(email="owner@wellstaq.test", hashed_password=hash_password("correct-horse-battery-staple"))
        db_session.add(admin)
        await db_session.flush()

        login = await client.post("/app-admin/auth/login", json={"email": "owner@wellstaq.test", "password": "correct-horse-battery-staple"})
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = await client.get("/app-admin/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["role"] == "super_admin"
        assert "organizations.manage" in me.json()["permissions"]

    @pytest.mark.asyncio
    async def test_login_wrong_password_401(self, client, db_session: AsyncSession):
        admin = make_admin_user(email="owner@wellstaq.test", hashed_password=hash_password("correct-horse-battery-staple"))
        db_session.add(admin)
        await db_session.flush()

        response = await client.post("/app-admin/auth/login", json={"email": "owner@wellstaq.test", "password": "wrong"})
        assert response.status_code == 401


class TestAdminOrganizationRoutes:
    @pytest.mark.asyncio
    async def test_suspend_requires_organizations_manage_permission(self, client, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)
        _, support_headers = await _admin_context(db_session, email="support@wellstaq.test", role="support")

        response = await client.post(f"/app-admin/organizations/{org.id}/suspend", headers=support_headers, json={"reason": "Testing permission enforcement."})
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_can_suspend(self, client, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)
        _, admin_headers = await _admin_context(db_session)

        response = await client.post(f"/app-admin/organizations/{org.id}/suspend", headers=admin_headers, json={"reason": "Testing suspend via HTTP."})
        assert response.status_code == 200
        assert response.json()["status"] == "suspended"

    @pytest.mark.asyncio
    async def test_suspend_requires_a_reason_of_at_least_10_chars(self, client, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)
        _, admin_headers = await _admin_context(db_session)

        response = await client.post(f"/app-admin/organizations/{org.id}/suspend", headers=admin_headers, json={"reason": "short"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_suspend_blocks_the_organizations_own_users_immediately(self, client, db_session: AsyncSession):
        """End-to-end across identities: an admin suspending an org via
        /app-admin/* must immediately invalidate that org's own users'
        existing sessions (app/auth/dependencies.get_current_user)."""
        org, owner = await make_org_with_owner(db_session)
        org_headers, _ = await authenticate_user(db_session, owner)
        _, admin_headers = await _admin_context(db_session)

        still_working = await client.get("/auth/me", headers=org_headers)
        assert still_working.status_code == 200

        suspend = await client.post(f"/app-admin/organizations/{org.id}/suspend", headers=admin_headers, json={"reason": "End-to-end suspension test."})
        assert suspend.status_code == 200

        now_blocked = await client.get("/auth/me", headers=org_headers)
        assert now_blocked.status_code == 403

    @pytest.mark.asyncio
    async def test_comp_and_revoke_round_trip(self, client, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)
        _, admin_headers = await _admin_context(db_session)

        comped = await client.post(f"/app-admin/organizations/{org.id}/comp", headers=admin_headers)
        assert comped.status_code == 200
        assert comped.json()["platform_comped"] is True

        revoked = await client.post(f"/app-admin/organizations/{org.id}/comp/revoke", headers=admin_headers)
        assert revoked.status_code == 200
        assert revoked.json()["platform_comped"] is False

    @pytest.mark.asyncio
    async def test_list_organizations_requires_authentication(self, client):
        response = await client.get("/app-admin/organizations")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_org_user_token_rejected_on_admin_routes(self, client, db_session: AsyncSession):
        _, owner = await make_org_with_owner(db_session)
        org_headers, _ = await authenticate_user(db_session, owner)

        response = await client.get("/app-admin/organizations", headers=org_headers)
        assert response.status_code == 401


class TestUserSearchAndActionsRoutes:
    @pytest.mark.asyncio
    async def test_search_requires_authentication(self, client):
        response = await client.get("/app-admin/users/search", params={"q": "findme"})
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_support_role_can_search_users(self, client, db_session: AsyncSession):
        _org, owner = await make_org_with_owner(db_session, user_kwargs={"email": "findme@example.com"})
        _, support_headers = await _admin_context(db_session, email="support@wellstaq.test", role="support")

        response = await client.get("/app-admin/users/search", headers=support_headers, params={"q": "findme"})
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == owner.id

    @pytest.mark.asyncio
    async def test_search_query_too_short_is_rejected(self, client, db_session: AsyncSession):
        _, admin_headers = await _admin_context(db_session)
        response = await client.get("/app-admin/users/search", headers=admin_headers, params={"q": "a"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_suspend_reactivate_password_reset_round_trip(self, client, db_session: AsyncSession, monkeypatch):
        from app.services.user_setting import password_service

        async def _no_op_send(**_kwargs):
            return None

        monkeypatch.setattr(password_service, "_send_reset_email", _no_op_send)

        _org, owner = await make_org_with_owner(db_session)
        _, admin_headers = await _admin_context(db_session)

        suspend = await client.post(f"/app-admin/users/{owner.id}/suspend", headers=admin_headers, json={"reason": "Support investigation."})
        assert suspend.status_code == 204

        reactivate = await client.post(f"/app-admin/users/{owner.id}/reactivate", headers=admin_headers)
        assert reactivate.status_code == 204

        password_reset = await client.post(f"/app-admin/users/{owner.id}/password-reset", headers=admin_headers)
        assert password_reset.status_code == 204

        log = await client.get("/app-admin/audit-log", headers=admin_headers)
        actions = {item["action"] for item in log.json()["items"]}
        assert {"user.suspended", "user.reactivated"} <= actions

    @pytest.mark.asyncio
    async def test_support_role_cannot_suspend_a_user(self, client, db_session: AsyncSession):
        _org, owner = await make_org_with_owner(db_session)
        _, support_headers = await _admin_context(db_session, email="support@wellstaq.test", role="support")

        response = await client.post(f"/app-admin/users/{owner.id}/suspend", headers=support_headers, json={"reason": "Should be blocked."})
        assert response.status_code == 403


class TestAdminTeamRoutes:
    @pytest.mark.asyncio
    async def test_invite_requires_admins_manage_permission(self, client, db_session: AsyncSession):
        _, support_headers = await _admin_context(db_session, email="support@wellstaq.test", role="support")
        response = await client.post("/app-admin/admins/invite", headers=support_headers, json={"email": "new@wellstaq.test", "role": "support"})
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_can_invite(self, client, db_session: AsyncSession):
        _, admin_headers = await _admin_context(db_session)
        response = await client.post("/app-admin/admins/invite", headers=admin_headers, json={"email": "new@wellstaq.test", "role": "admin"})
        assert response.status_code == 200
        assert response.json()["status"] == "invited"

    @pytest.mark.asyncio
    async def test_cannot_remove_self_via_http(self, client, db_session: AsyncSession):
        admin, admin_headers = await _admin_context(db_session)
        response = await client.delete(f"/app-admin/admins/{admin.id}", headers=admin_headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_invite_is_recorded_in_audit_log_with_inviting_admin_as_actor(self, client, db_session: AsyncSession):
        inviter, admin_headers = await _admin_context(db_session, email="inviter@wellstaq.test")

        invite = await client.post("/app-admin/admins/invite", headers=admin_headers, json={"email": "new@wellstaq.test", "role": "admin"})
        assert invite.status_code == 200

        log = await client.get("/app-admin/audit-log", headers=admin_headers)
        assert log.status_code == 200
        entry = next(item for item in log.json()["items"] if item["action"] == "admin.invited")
        assert entry["target_label"] == "new@wellstaq.test"
        assert entry["actor"]["email"] == inviter.email


class TestAdminAuditLogRoute:
    @pytest.mark.asyncio
    async def test_suspend_action_appears_in_audit_log(self, client, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)
        _, admin_headers = await _admin_context(db_session)

        await client.post(f"/app-admin/organizations/{org.id}/suspend", headers=admin_headers, json={"reason": "Audit log coverage test."})

        log = await client.get("/app-admin/audit-log", headers=admin_headers)
        assert log.status_code == 200
        assert log.json()["total"] == 1
        assert log.json()["items"][0]["action"] == "organization.suspended"

    @pytest.mark.asyncio
    async def test_date_range_filters_entries_outside_the_window(self, client, db_session: AsyncSession):
        from datetime import datetime, timedelta, timezone

        from app.models import AuditEntry

        _, admin_headers = await _admin_context(db_session)
        now = datetime.now(timezone.utc)
        db_session.add(AuditEntry(action="organization.suspended", target_type="organization", target_id="org-old", target_label="Old Co", created_at=now - timedelta(days=30)))
        db_session.add(AuditEntry(action="organization.suspended", target_type="organization", target_id="org-recent", target_label="Recent Co", created_at=now))
        await db_session.flush()

        unfiltered = await client.get("/app-admin/audit-log", headers=admin_headers)
        assert unfiltered.json()["total"] == 2

        start_date = (now - timedelta(days=1)).date().isoformat()
        filtered = await client.get("/app-admin/audit-log", headers=admin_headers, params={"start_date": start_date})
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["target_label"] == "Recent Co"

        end_date = (now - timedelta(days=29)).date().isoformat()
        bounded = await client.get("/app-admin/audit-log", headers=admin_headers, params={"end_date": end_date})
        assert bounded.status_code == 200
        assert bounded.json()["total"] == 1
        assert bounded.json()["items"][0]["target_label"] == "Old Co"
