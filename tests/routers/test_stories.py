"""HTTP route tests for app/routers/stories.py."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Story
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession, *, role_name="member"):
    org, user = await make_org_with_owner(db_session, user_kwargs={"role_name": role_name})
    headers, _ = await authenticate_user(
        db_session, user, device_fingerprint=f"story-router-{user.id}"
    )
    return org, user, headers


async def _story(db_session: AsyncSession, user, **overrides):
    values = dict(
        user_id=user.id,
        organization_id=user.organization_id,
        branch_id=user.branch_id,
        media_url="https://cdn.example.com/stories/test.jpg",
        media_type="image",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    row = Story(**{**values, **overrides})
    db_session.add(row)
    await db_session.flush()
    return row


class TestStoryRoutes:
    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/stories"
        )
        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_create_list_get_my_stories_and_delete(
        self, client, db_session: AsyncSession, monkeypatch
    ):
        org, user, headers = await _context(db_session)
        create_story_module = importlib.import_module("app.services.story.create_story")
        monkeypatch.setattr(create_story_module, "confirm_upload", lambda **_: True)

        created = await client.post(
            f"/organizations/{org.id}/stories",
            headers=headers,
            json={
                "branch_id": user.branch_id,
                "media_url": "https://cdn.example.com/stories/new.jpg",
                "media_type": "image",
            },
        )
        assert created.status_code == 201
        story_id = created.json()["id"]
        stored = (await db_session.execute(select(Story).where(Story.id == story_id))).scalar_one()
        assert stored.user_id == user.id

        listed = await client.get(
            f"/organizations/{org.id}/stories?branch_id={user.branch_id}", headers=headers
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        mine = await client.get(f"/organizations/{org.id}/stories/me", headers=headers)
        assert mine.status_code == 200
        assert mine.json()["items"][0]["id"] == story_id

        fetched = await client.get(
            f"/organizations/{org.id}/stories/{story_id}", headers=headers
        )
        assert fetched.status_code == 200
        assert fetched.json()["media_url"].endswith("new.jpg")

        deleted = await client.delete(
            f"/organizations/{org.id}/stories/{story_id}", headers=headers
        )
        assert deleted.status_code == 204
        assert (await db_session.execute(select(Story).where(Story.id == story_id))).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_expired_story_is_excluded_from_list(self, client, db_session: AsyncSession):
        org, user, headers = await _context(db_session)
        await _story(
            db_session,
            user,
            created_at=datetime.now(timezone.utc) - timedelta(hours=25),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        response = await client.get(f"/organizations/{org.id}/stories", headers=headers)

        assert response.status_code == 200
        assert response.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_rejects_different_organization(self, client, db_session: AsyncSession):
        _, _, headers = await _context(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "story-other", "business_name": "Story Other"},
            user_kwargs={"email": "story-other@example.com"},
        )

        response = await client.get(f"/organizations/{other_org.id}/stories", headers=headers)

        assert response.status_code == 403


@pytest.mark.asyncio
async def test_story_likes_are_idempotent_and_expires(client, db_session: AsyncSession):
    org, user, headers = await _context(db_session)
    story = await _story(db_session, user)
    endpoint = f"/organizations/{org.id}/stories/{story.id}/like"

    liked = await client.post(endpoint, headers=headers)
    assert liked.status_code == 200
    assert liked.json()["like_count"] == 1
    again = await client.post(endpoint, headers=headers)
    assert again.status_code == 200
    assert again.json()["like_count"] == 1

    fetched = await client.get(f"/organizations/{org.id}/stories/{story.id}", headers=headers)
    assert fetched.json()["like_count"] == 1

    unliked = await client.delete(endpoint, headers=headers)
    assert unliked.status_code == 200
    assert unliked.json()["like_count"] == 0
    again = await client.delete(endpoint, headers=headers)
    assert again.status_code == 200
    assert again.json()["like_count"] == 0

    story.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    story.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.flush()
    expired = await client.post(endpoint, headers=headers)
    assert expired.status_code == 404


@pytest.mark.asyncio
async def test_video_story_upload_contract(client, db_session: AsyncSession, monkeypatch):
    org, user, headers = await _context(db_session)
    service = importlib.import_module("app.services.story.create_story")
    monkeypatch.setattr(service, "confirm_upload", lambda **_: True)
    video_url = f"https://assets.test.invalid/stories/{org.id}/clip.mp4"

    response = await client.post(f"/organizations/{org.id}/stories", headers=headers,
                                 json={"media_url": video_url, "media_type": "video"})
    assert response.status_code == 201, response.text
    assert response.json()["media_type"] == "video"
    assert response.json()["media_url"] == video_url

    wrong_type = await client.post(f"/organizations/{org.id}/stories", headers=headers,
                                   json={"media_url": video_url, "media_type": "image"})
    assert wrong_type.status_code == 400
