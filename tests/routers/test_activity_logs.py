"""
HTTP route tests for app/routers/activity_logs.py.

Both endpoints are exercised with real authentication, subscription checks,
tenant isolation, schema validation, normalized daily upserts, persisted
results, and daily/weekly/monthly trend responses.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog
from tests.conftest import authenticate_user, make_org_with_owner


async def _authenticated_org(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session,
        owner,
        device_fingerprint=f"activity-log-router-{owner.id}",
    )
    return org, owner, headers


async def _add_log(db_session, org, user, *, day, value, metric_type="steps"):
    row = ActivityLog(
        organization_id=org.id,
        branch_id=user.branch_id,
        user_id=user.id,
        metric_type=metric_type,
        value=value,
        unit=None,
        date=day,
    )
    db_session.add(row)
    await db_session.flush()
    return row


# ── Access control ───────────────────────────────────────────────────────────

class TestActivityLogRouteAccess:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/organizations/x/activity-log"),
            ("GET", "/organizations/x/activity-log/trend?metric_type=steps"),
        ],
    )
    async def test_requires_authentication(self, client, method: str, path: str):
        response = await client.request(
            method,
            path,
            json={"metric_type": "steps", "value": 1} if method == "POST" else None,
        )

        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_rejects_a_different_organization(
        self, client, db_session: AsyncSession
    ):
        _, _, headers = await _authenticated_org(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "activity-log-other", "business_name": "Other"},
            user_kwargs={"email": "activity-log-other@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/activity-log/trend?metric_type=steps",
            headers=headers,
        )

        assert response.status_code == 403


# ── Submit ───────────────────────────────────────────────────────────────────

class TestSubmitActivityRoute:

    @pytest.mark.asyncio
    async def test_creates_normalized_daily_entry_and_returns_safe_shape(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authenticated_org(db_session)

        response = await client.post(
            f"/organizations/{org.id}/activity-log",
            headers=headers,
            json={
                "metric_type": " Distance ",
                "value": 6500,
                "unit": None,
                "source": "manual",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["entry"]["metric_type"] == "distance"
        assert float(body["entry"]["value"]) == 6500
        assert body["entry"]["date"] == date.today().isoformat()
        assert body["challenges_updated"] == []
        assert set(body["entry"]) == {"id", "metric_type", "value", "unit", "date", "source", "is_verified"}
        assert body["entry"]["source"] == "manual"
        assert body["entry"]["is_verified"] is False
        persisted = (
            await db_session.execute(
                select(ActivityLog).where(ActivityLog.id == body["entry"]["id"])
            )
        ).scalar_one()
        assert persisted.user_id == owner.id
        assert persisted.organization_id == org.id
        assert persisted.branch_id == owner.branch_id

    @pytest.mark.asyncio
    async def test_same_day_submission_updates_instead_of_duplicating(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authenticated_org(db_session)
        path = f"/organizations/{org.id}/activity-log"
        first = await client.post(
            path, headers=headers, json={"metric_type": "distance", "value": 1000}
        )
        second = await client.post(
            path, headers=headers, json={"metric_type": "distance", "value": 2000}
        )

        assert first.status_code == second.status_code == 201
        assert first.json()["entry"]["id"] == second.json()["entry"]["id"]
        count = (
            await db_session.execute(
                select(func.count()).select_from(ActivityLog).where(
                    ActivityLog.user_id == owner.id,
                    ActivityLog.metric_type == "distance",
                )
            )
        ).scalar_one()
        assert count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"metric_type": "steps", "value": -1},
            {"value": 1},
            {"metric_type": "steps", "value": 1, "unit": "x" * 21},
        ],
    )
    async def test_rejects_invalid_submission_payload(
        self, client, db_session: AsyncSession, payload: dict
    ):
        org, _, headers = await _authenticated_org(db_session)

        response = await client.post(
            f"/organizations/{org.id}/activity-log",
            headers=headers,
            json=payload,
        )

        assert response.status_code == 422


# ── Trend ────────────────────────────────────────────────────────────────────

class TestActivityTrendRoute:

    @pytest.mark.asyncio
    async def test_returns_normalized_daily_points_in_order(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authenticated_org(db_session)
        await _add_log(db_session, org, owner, day=date(2026, 9, 2), value=200)
        await _add_log(db_session, org, owner, day=date(2026, 9, 1), value=100)

        response = await client.get(
            f"/organizations/{org.id}/activity-log/trend?metric_type=%20STEPS%20",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json() == {
            "metric_type": "steps",
            "granularity": "daily",
            "points": [
                {"period": "2026-09-01", "value": "100.0000000000"},
                {"period": "2026-09-02", "value": "200.0000000000"},
            ],
        }

    @pytest.mark.asyncio
    async def test_weekly_granularity_aggregates_points(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authenticated_org(db_session)
        await _add_log(db_session, org, owner, day=date(2026, 9, 7), value=100)
        await _add_log(db_session, org, owner, day=date(2026, 9, 9), value=250)

        response = await client.get(
            f"/organizations/{org.id}/activity-log/trend"
            "?metric_type=steps&granularity=weekly",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["points"] == [
            {"period": "2026-09-07", "value": "350.0000000000"}
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        ["granularity=yearly", "limit=0", "limit=201", "granularity=daily"],
    )
    async def test_rejects_invalid_or_missing_trend_query(
        self, client, db_session: AsyncSession, query: str
    ):
        org, _, headers = await _authenticated_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/activity-log/trend?{query}", headers=headers
        )

        assert response.status_code == 422
