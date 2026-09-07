"""HTTP route tests for app/routers/chats.py."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupChatMessage
from app.services.club.create_club import create_club
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session, user, device_fingerprint=f"chat-router-{user.id}"
    )
    club = await create_club(
        session=db_session,
        organization_id=org.id,
        branch_id=user.branch_id,
        name="Running Club",
        description=None,
        image_url=None,
        privacy="public",
        category="fitness",
        created_by=user.id,
    )
    return org, user, club, headers


class TestChatRoutes:
    @pytest.mark.asyncio
    async def test_send_list_pin_unpin_and_delete_message(
        self, client, db_session: AsyncSession
    ):
        org, user, club, headers = await _context(db_session)
        base = f"/organizations/{org.id}/conversations"

        sent = await client.post(
            f"{base}/club/{club.id}/messages",
            headers=headers,
            json={"content": "  Good morning team  "},
        )
        assert sent.status_code == 201
        message_id = sent.json()["id"]
        assert sent.json()["content"] == "Good morning team"

        listed = await client.get(
            f"{base}/club/{club.id}/messages?limit=1", headers=headers
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["user_id"] == user.id

        pinned = await client.post(f"{base}/messages/{message_id}/pin", headers=headers)
        assert pinned.status_code == 200
        assert pinned.json()["is_pinned"] is True

        unpinned = await client.delete(f"{base}/messages/{message_id}/pin", headers=headers)
        assert unpinned.status_code == 200
        assert unpinned.json()["is_pinned"] is False

        deleted = await client.delete(f"{base}/messages/{message_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["message_id"] == message_id
        assert (
            await db_session.execute(
                select(GroupChatMessage).where(GroupChatMessage.id == message_id)
            )
        ).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_send_rejects_empty_message(self, client, db_session: AsyncSession):
        org, _, club, headers = await _context(db_session)

        response = await client.post(
            f"/organizations/{org.id}/conversations/club/{club.id}/messages",
            headers=headers,
            json={"content": "   "},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_send_requires_conversation_membership(self, client, db_session: AsyncSession):
        org, _, _, headers = await _context(db_session)

        response = await client.post(
            f"/organizations/{org.id}/conversations/club/missing-club/messages",
            headers=headers,
            json={"content": "Hello"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_different_organization(self, client, db_session: AsyncSession):
        _, _, _, headers = await _context(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "chat-other", "business_name": "Chat Other"},
            user_kwargs={"email": "chat-other@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/conversations/club/missing/messages",
            headers=headers,
        )

        assert response.status_code == 403
