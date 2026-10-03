from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import ActivityLog, HealthActivitySync
from tests.conftest import authenticate_user, make_org_with_owner


async def authenticated_org(db_session):
    org, owner = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session, owner, device_fingerprint=f"health-sync-{owner.id}",
    )
    return org, owner, headers


def record(metric_type: str, value: float, *, record_id: str, method: str = "automatic"):
    now = datetime.now(timezone.utc)
    return {
        "record_id": record_id,
        "metric_type": metric_type,
        "value": value,
        "recorded_date": date.today().isoformat(),
        "started_at": now.replace(hour=6).isoformat(),
        "ended_at": now.replace(hour=7).isoformat(),
        "recording_method": method,
        "source_name": "Test phone",
        "source_id": "test.health",
    }


@pytest.mark.asyncio
async def test_protected_metrics_cannot_be_entered_manually(client, db_session):
    org, _, headers = await authenticated_org(db_session)

    response = await client.post(
        f"/organizations/{org.id}/activity-log",
        headers=headers,
        json={"metric_type": "steps", "value": 5000, "source": "manual"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_health_sync_is_verified_and_idempotent(client, db_session):
    org, owner, headers = await authenticated_org(db_session)
    path = f"/organizations/{org.id}/activity-log/health-sync"
    payload = {
        "provider": "health_connect",
        "device_id": "device-1",
        "records": [record("steps", 4200, record_id="daily:steps:today")],
    }

    first = await client.post(path, headers=headers, json=payload)
    payload["records"][0]["value"] = 5100
    second = await client.post(path, headers=headers, json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json()["entries"][0]["id"] == second.json()["entries"][0]["id"]
    assert second.json()["entries"][0]["is_verified"] is True
    assert second.json()["entries"][0]["source"] == "health_connect"
    log = (await db_session.execute(select(ActivityLog).where(
        ActivityLog.user_id == owner.id, ActivityLog.metric_type == "steps",
    ))).scalar_one()
    assert float(log.value) == 5100
    assert await db_session.scalar(select(func.count()).select_from(HealthActivitySync)) == 1


@pytest.mark.asyncio
async def test_multiple_measured_runs_are_summed_for_the_day(client, db_session):
    org, owner, headers = await authenticated_org(db_session)
    path = f"/organizations/{org.id}/activity-log/health-sync"
    base = {"provider": "apple_health", "device_id": "iphone-1"}

    first = await client.post(path, headers=headers, json={
        **base, "records": [record("run", 2.5, record_id="run:one", method="active")],
    })
    second = await client.post(path, headers=headers, json={
        **base, "records": [record("run", 3.25, record_id="run:two", method="active")],
    })

    assert first.status_code == second.status_code == 200
    log = (await db_session.execute(select(ActivityLog).where(
        ActivityLog.user_id == owner.id, ActivityLog.metric_type == "run",
    ))).scalar_one()
    assert float(log.value) == 5.75
    assert await db_session.scalar(select(func.count()).select_from(HealthActivitySync)) == 2
