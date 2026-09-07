"""HTTP route tests for app/routers/engagement_logs.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import authenticate_user, make_org_with_owner


async def _headers(db_session: AsyncSession):
    _, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"engagement-log-router-{user.id}")
    return headers


class TestEngagementLogRoutes:
    @pytest.mark.asyncio
    async def test_empty_trend_and_snapshot_before_worker_runs(self, client, db_session):
        headers = await _headers(db_session)
        trend = await client.get("/engagement-log/trend?metric_type=PARTICIPATION", headers=headers)
        assert trend.status_code == 200
        assert trend.json() == {"metric_type": "participation", "points": []}

        snapshot = await client.get("/engagement-log/snapshot", headers=headers)
        assert snapshot.status_code == 200
        assert snapshot.json()["participation"] is None
        assert snapshot.json()["checkins"] is None

    @pytest.mark.asyncio
    async def test_rejects_unknown_metric(self, client, db_session):
        headers = await _headers(db_session)
        response = await client.get("/engagement-log/trend?metric_type=unknown", headers=headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get("/engagement-log/snapshot")
        assert response.status_code in {401, 403}
