"""
app/services/integration/fit_sync.py

Pulls daily step aggregates from the Google Fit REST API for a connected
UserIntegration and feeds them through the EXISTING health-activity
pipeline (app/services/activity_log/sync_health_activity.py) — this is
the same path native HealthKit/Health Connect syncs go through, so
automatic challenge tracking, HealthActivitySync auditing, and duplicate
detection all just work without any new code there.

Only steps today — distance/calories aggregation buckets use a different
Fit API data source id and are left for a follow-up once steps are
confirmed working end-to-end with real credentials.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserIntegration
from app.schemas.activity_log import HealthActivityRecord
from app.services.activity_log.sync_health_activity import sync_health_activity
from app.services.integration.crypto import decrypt_token, encrypt_token
from app.services.integration.oauth_client import refresh_access_token

FIT_AGGREGATE_ENDPOINT = "https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate"
STEPS_DATA_SOURCE = "derived:com.google.step_count.delta:com.google.android.gms:estimated_steps"
MAX_SYNC_DAYS = 7


async def _get_valid_access_token(*, session: AsyncSession, user_integration: UserIntegration) -> str:
    now = datetime.now(timezone.utc)
    if user_integration.token_expires_at and user_integration.token_expires_at > now + timedelta(minutes=2):
        return decrypt_token(user_integration.encrypted_access_token)

    if not user_integration.encrypted_refresh_token:
        raise HTTPException(status.HTTP_409_CONFLICT, "Google access has expired and no refresh token is on file — please reconnect.")

    tokens = await refresh_access_token("google", decrypt_token(user_integration.encrypted_refresh_token))
    user_integration.encrypted_access_token = encrypt_token(tokens["access_token"])
    user_integration.token_expires_at = now + timedelta(seconds=tokens.get("expires_in", 3600))
    await session.flush()
    return tokens["access_token"]


async def sync_google_fit_steps(
    *, session: AsyncSession, organization_id: str, branch_id: str, user_id: str, days: int = 3,
) -> tuple[list, list[str]]:
    """Pulls the last `days` days of step counts and upserts them as
    verified activity log entries via the shared sync_health_activity
    pipeline. Returns (entries, challenge_ids) exactly like that
    function does, so a router can report which challenges updated."""
    days = min(days, MAX_SYNC_DAYS)

    result = await session.execute(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id, UserIntegration.provider == "google", UserIntegration.enabled.is_(True),
        )
    )
    user_integration = result.scalar_one_or_none()
    if user_integration is None or user_integration.status != "connected":
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect Google Fit before syncing activity.")

    access_token = await _get_valid_access_token(session=session, user_integration=user_integration)

    today = date.today()
    start_of_range = today - timedelta(days=days - 1)
    start_ms = int(datetime.combine(start_of_range, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            FIT_AGGREGATE_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "aggregateBy": [{"dataTypeName": "com.google.step_count.delta", "dataSourceId": STEPS_DATA_SOURCE}],
                "bucketByTime": {"durationMillis": 86_400_000},  # one bucket per day
                "startTimeMillis": start_ms,
                "endTimeMillis": end_ms,
            },
        )
    if response.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Google Fit request failed: {response.text}")

    records: list[HealthActivityRecord] = []
    for bucket in response.json().get("bucket", []):
        bucket_start = datetime.fromtimestamp(int(bucket["startTimeMillis"]) / 1000, tz=timezone.utc)
        bucket_end = datetime.fromtimestamp(int(bucket["endTimeMillis"]) / 1000, tz=timezone.utc)
        steps = sum(
            point.get("value", [{}])[0].get("intVal", 0)
            for dataset in bucket.get("dataset", [])
            for point in dataset.get("point", [])
        )
        if steps <= 0:
            continue
        records.append(HealthActivityRecord(
            record_id=f"google_fit:steps:{bucket_start.date().isoformat()}:{user_id}",
            metric_type="steps",
            value=steps,
            recorded_date=bucket_start.date(),
            started_at=bucket_start,
            ended_at=bucket_end,
            recording_method="automatic",
            source_name="Google Fit",
        ))

    if not records:
        return [], []

    user_integration.last_synced_at = datetime.now(timezone.utc)
    await session.flush()

    return await sync_health_activity(
        session=session, organization_id=organization_id, branch_id=branch_id, user_id=user_id,
        provider="google_fit", device_id="google_fit", records=records,
    )
