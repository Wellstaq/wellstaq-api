"""HTTP route tests for app/routers/hashtags.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hashtag
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"hashtag-router-{user.id}")
    return org, headers


class TestHashtagRoutes:
    @pytest.mark.asyncio
    async def test_search_trending_and_posts_routes(self, client, db_session):
        org, headers = await _context(db_session)
        hashtag = Hashtag(organization_id=org.id, tag_name="wellness", total_post_count=0)
        db_session.add(hashtag)
        await db_session.flush()

        searched = await client.get(
            f"/organizations/{org.id}/hashtags/search?q=well", headers=headers
        )
        assert searched.status_code == 200
        assert searched.json()["total"] == 1
        assert searched.json()["items"][0]["tag_name"] == "wellness"

        trending = await client.get(f"/organizations/{org.id}/hashtags/trending", headers=headers)
        assert trending.status_code == 200
        assert trending.json()["items"] == []

        posts = await client.get(
            f"/organizations/{org.id}/hashtags/wellness/posts", headers=headers
        )
        assert posts.status_code == 200
        assert posts.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_search_rejects_empty_query(self, client, db_session):
        org, headers = await _context(db_session)
        response = await client.get(f"/organizations/{org.id}/hashtags/search?q=", headers=headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_different_organization(self, client, db_session):
        _, headers = await _context(db_session)
        other, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "hashtag-other", "business_name": "Hashtag Other"},
            user_kwargs={"email": "hashtag-other@example.com"},
        )
        response = await client.get(
            f"/organizations/{other.id}/hashtags/search?q=well", headers=headers
        )
        assert response.status_code == 403
