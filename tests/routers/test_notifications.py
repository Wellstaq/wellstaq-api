"""HTTP route tests for app/routers/notifications.py."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MobilePushToken, Notification
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session, user, device_fingerprint=f"notification-router-{user.id}"
    )
    return org, user, headers


async def _notification(db_session: AsyncSession, user, *, body="Welcome"):
    row = Notification(
        user_id=user.id,
        organization_id=user.organization_id,
        notification_type="announcement",
        body=body,
        is_read=False,
    )
    db_session.add(row)
    await db_session.flush()
    return row


class TestPushTokenRoutes:
    @pytest.mark.asyncio
    async def test_register_updates_and_unregisters_own_token(self, client, db_session: AsyncSession):
        _, user, headers = await _context(db_session)
        token = "ExponentPushToken[router-test-token]"

        created = await client.put(
            "/notifications/push-tokens",
            headers=headers,
            json={"token": token, "provider": "expo", "platform": "android", "device_id": "phone-1"},
        )
        assert created.status_code == 200
        assert created.json()["is_active"] is True

        updated = await client.put(
            "/notifications/push-tokens",
            headers=headers,
            json={"token": token, "provider": "expo", "platform": "ios", "device_id": "phone-2"},
        )
        assert updated.status_code == 200
        assert updated.json()["platform"] == "ios"

        deleted = await client.delete(
            "/notifications/push-tokens", headers=headers, params={"token": token}
        )
        assert deleted.status_code == 204
        row = (await db_session.execute(select(MobilePushToken).where(MobilePushToken.token == token))).scalar_one()
        assert row.user_id == user.id
        assert row.is_active is False

    @pytest.mark.asyncio
    async def test_register_rejects_invalid_token(self, client, db_session: AsyncSession):
        _, _, headers = await _context(db_session)
        response = await client.put(
            "/notifications/push-tokens",
            headers=headers,
            json={"token": "short", "platform": "android"},
        )
        assert response.status_code == 422


class TestNotificationReadRoutes:
    @pytest.mark.asyncio
    async def test_list_count_mark_one_and_delete(self, client, db_session: AsyncSession):
        _, user, headers = await _context(db_session)
        row = await _notification(db_session, user)

        listed = await client.get("/notifications?unread_only=true", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["unread_count"] == 1

        count = await client.get("/notifications/unread-count", headers=headers)
        assert count.status_code == 200
        assert count.json()["unread_count"] == 1

        marked = await client.patch(f"/notifications/{row.id}/read", headers=headers)
        assert marked.status_code == 200
        assert marked.json()["is_read"] is True
        await db_session.refresh(row)
        assert row.read_at is not None

        deleted = await client.delete(f"/notifications/{row.id}", headers=headers)
        assert deleted.status_code == 204
        assert (await db_session.execute(select(Notification).where(Notification.id == row.id))).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_mark_all_only_changes_current_users_notifications(self, client, db_session: AsyncSession):
        org, user, headers = await _context(db_session)
        first = await _notification(db_session, user, body="One")
        second = await _notification(db_session, user, body="Two")

        response = await client.patch("/notifications/read-all", headers=headers)

        assert response.status_code == 200
        assert response.json()["marked_count"] == 2
        await db_session.refresh(first)
        await db_session.refresh(second)
        assert first.is_read is True and second.is_read is True

    @pytest.mark.asyncio
    async def test_cannot_read_another_users_notification(self, client, db_session: AsyncSession):
        org, user, headers = await _context(db_session)
        other_org, other = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "notification-other", "business_name": "Notification Other"},
            user_kwargs={"email": "notification-other@example.com"},
        )
        row = await _notification(db_session, other)

        response = await client.patch(f"/notifications/{row.id}/read", headers=headers)

        assert response.status_code == 404
