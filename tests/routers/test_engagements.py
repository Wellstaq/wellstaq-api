"""HTTP route tests for app/routers/engagements.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    _, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"engagement-router-{user.id}")
    return headers


class TestEngagementRoutes:
    @pytest.mark.asyncio
    async def test_priority_and_checkin_workflow(self, client, db_session):
        headers = await _context(db_session)

        saved = await client.put(
            "/engagement/priorities", headers=headers, json={"priorities": ["reduce_stress", "stay_active"]}
        )
        assert saved.status_code == 200
        assert [item["rank"] for item in saved.json()["items"]] == [1, 2]

        priorities = await client.get("/engagement/priorities", headers=headers)
        assert priorities.status_code == 200
        assert [item["priority"] for item in priorities.json()["items"]] == ["reduce_stress", "stay_active"]

        checkin = await client.post(
            "/engagement/checkins",
            headers=headers,
            json={"mood": "good", "energy_level": "good", "stress_level": "bad", "notes": "Improving"},
        )
        assert checkin.status_code == 201
        assert checkin.json()["mood"] == "good"

        history = await client.get("/engagement/checkins", headers=headers)
        assert history.status_code == 200
        assert history.json()["total"] == 1

        streak = await client.get("/engagement/checkins/streak", headers=headers)
        assert streak.status_code == 200
        assert streak.json()["checked_in_today"] is True
        assert streak.json()["current_streak_days"] == 1

    @pytest.mark.asyncio
    async def test_priorities_reject_empty_list(self, client, db_session):
        headers = await _context(db_session)
        response = await client.put(
            "/engagement/priorities", headers=headers, json={"priorities": []}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get("/engagement/priorities")
        assert response.status_code in {401, 403}
