"""HTTP route tests for app/routers/activities.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserActivityFeed
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"activities-router-{user.id}")
    row = UserActivityFeed(
        user_id=user.id, organization_id=org.id, activity_type="created_post",
        reference_id="post-1", reference_type="post",
    )
    db_session.add(row)
    await db_session.flush()
    return org, user, headers


class TestActivityRoutes:
    @pytest.mark.asyncio
    async def test_feed_summary_and_user_count(self, client, db_session):
        org, user, headers = await _context(db_session)
        feed = await client.get(f"/organizations/{org.id}/activities/me", headers=headers)
        assert feed.status_code == 200
        assert feed.json()["total"] == 1
        assert feed.json()["items"][0]["activity_type"] == "created_post"

        summary = await client.get(f"/organizations/{org.id}/activities/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["total_count"] == 1
        assert summary.json()["by_type"][0] == {"activity_type": "created_post", "count": 1}

        count = await client.get(
            f"/organizations/{org.id}/activities/users/{user.id}/count", headers=headers
        )
        assert count.status_code == 200
        assert count.json() == {"user_id": user.id, "total_count": 1}

    @pytest.mark.asyncio
    async def test_summary_rejects_invalid_period(self, client, db_session):
        org, _, headers = await _context(db_session)
        response = await client.get(
            f"/organizations/{org.id}/activities/summary?period=quarter", headers=headers
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/activities/me"
        )
        assert response.status_code in {401, 403}
