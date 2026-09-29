"""Notification preference and recipient-isolation tests; mobile delivery mocked."""
import importlib
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Notification, UserSettings
from app.services.notification import create_notification
from app.services.notification.mark_read import mark_as_read, mark_all_as_read
from tests.conftest import make_org_with_owner, make_user_with_role


@pytest.fixture
def push_delivery(monkeypatch):
    sender = AsyncMock()
    module = importlib.import_module("app.services.notification.create_notification")
    monkeypatch.setattr(module, "send_mobile_push", sender)
    return sender


async def _settings(session, **overrides):
    org, user = await make_org_with_owner(session)
    session.add(UserSettings(user_id=user.id, organization_id=org.id, **overrides))
    await session.flush()
    return org, user


class TestNotificationPreferences:
    @pytest.mark.parametrize("enabled", [False, True])
    async def test_push_follows_user_preference_but_inbox_is_retained(self, db_session, push_delivery, enabled):
        org, user = await _settings(db_session, push_notifications=enabled)
        notification = await create_notification(session=db_session, user_id=user.id, organization_id=org.id, notification_type="announcement", body="Hello")
        assert notification.id is not None
        assert notification.is_read is False
        assert push_delivery.await_count == int(enabled)
        if enabled:
            assert push_delivery.await_args.kwargs["user_id"] == user.id
            assert push_delivery.await_args.kwargs["data"]["notification_id"] == notification.id

    async def test_disabled_challenge_reminders_create_no_inbox_or_push(self, db_session, push_delivery):
        org, user = await _settings(db_session, challenge_reminders=False, push_notifications=True)
        notification = await create_notification(session=db_session, user_id=user.id, organization_id=org.id, notification_type="challenge_completed", body="Completed")
        assert notification is None
        assert (await db_session.execute(select(Notification))).scalars().all() == []
        push_delivery.assert_not_awaited()

    async def test_disabling_challenge_reminders_does_not_disable_announcements(self, db_session, push_delivery):
        org, user = await _settings(db_session, challenge_reminders=False, push_notifications=False)
        result = await create_notification(session=db_session, user_id=user.id, organization_id=org.id, notification_type="announcement", body="News")
        assert result.body == "News"

    async def test_body_is_truncated_to_storage_limit(self, db_session, push_delivery):
        org, user = await _settings(db_session, push_notifications=False)
        result = await create_notification(session=db_session, user_id=user.id, organization_id=org.id, notification_type="announcement", body="x" * 600)
        assert result.body == "x" * 500

    @pytest.mark.parametrize("reference", [{"reference_id": "id-only"}, {"reference_type": "event"}, {"reference_id": "id", "reference_type": "invalid"}])
    async def test_invalid_reference_is_rejected(self, db_session, push_delivery, reference):
        with pytest.raises(ValueError):
            await create_notification(session=db_session, user_id="unused", notification_type="announcement", body="News", **reference)
        push_delivery.assert_not_awaited()


class TestNotificationReadIsolation:
    async def test_read_is_idempotent_and_other_user_cannot_read_it(self, db_session, push_delivery):
        org, user = await _settings(db_session, push_notifications=False)
        other = await make_user_with_role(db_session, org.id, email="other@example.com", branch_id=user.branch_id)
        db_session.add(other)
        await db_session.flush()
        notification = await create_notification(session=db_session, user_id=user.id, organization_id=org.id, notification_type="announcement", body="News")
        with pytest.raises(HTTPException) as exc:
            await mark_as_read(session=db_session, user_id=other.id, notification_id=notification.id)
        assert exc.value.status_code == 404
        assert notification.is_read is False
        result = await mark_as_read(session=db_session, user_id=user.id, notification_id=notification.id)
        first_read = result.read_at
        assert first_read is not None
        assert first_read.tzinfo is None
        result = await mark_as_read(session=db_session, user_id=user.id, notification_id=notification.id)
        assert result.read_at == first_read
        assert result.is_read is True

    async def test_mark_all_only_changes_current_recipient(self, db_session, push_delivery):
        org, user = await _settings(db_session, push_notifications=False)
        other = await make_user_with_role(db_session, org.id, email="other@example.com", branch_id=user.branch_id)
        db_session.add(other)
        await db_session.flush()
        mine = await create_notification(session=db_session, user_id=user.id, notification_type="announcement", body="Mine")
        theirs = await create_notification(session=db_session, user_id=other.id, notification_type="announcement", body="Theirs")
        assert await mark_all_as_read(session=db_session, user_id=user.id) == 1
        await db_session.refresh(mine)
        await db_session.refresh(theirs)
        assert mine.is_read is True
        assert mine.read_at is not None
        assert mine.read_at.tzinfo is None
        assert theirs.is_read is False
        assert await mark_all_as_read(session=db_session, user_id=user.id) == 0
