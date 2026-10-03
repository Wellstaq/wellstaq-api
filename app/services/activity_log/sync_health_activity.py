"""Idempotently ingest trusted aggregates read from native health stores."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HealthActivitySync
from app.schemas.activity_log import HealthActivityRecord
from app.services.activity_log._helpers import today_utc
from app.services.activity_log.submit_activity import submit_activity

HEALTH_ACTIVITY_UNITS = {"steps": "count", "run": "km", "calories": "kcal"}
MAX_BACKFILL_DAYS = 30


async def sync_health_activity(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    user_id: str,
    provider: str,
    device_id: str,
    records: list[HealthActivityRecord],
) -> tuple[list, list[str]]:
    today = today_utc()
    entries = []
    challenge_ids: set[str] = set()

    for record in sorted(records, key=lambda item: (item.recorded_date, item.metric_type)):
        age = (today - record.recorded_date).days
        if age < 0 or age > MAX_BACKFILL_DAYS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"recorded_date must be within the last {MAX_BACKFILL_DAYS} days",
            )

        result = await session.execute(
            select(HealthActivitySync).where(
                HealthActivitySync.user_id == user_id,
                HealthActivitySync.provider == provider,
                HealthActivitySync.external_id == record.record_id,
            )
        )
        audit = result.scalar_one_or_none()
        if audit is not None and (
            audit.metric_type != record.metric_type
            or audit.recorded_date != record.recorded_date
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "record_id is already attached to a different health aggregate",
            )

        unit = HEALTH_ACTIVITY_UNITS[record.metric_type]
        entry, updated = await submit_activity(
            session=session,
            organization_id=organization_id,
            branch_id=branch_id,
            user_id=user_id,
            metric_type=record.metric_type,
            value=record.value,
            unit=unit,
            source=provider,
            entry_date=record.recorded_date,
            is_verified=True,
        )

        values = {
            "organization_id": organization_id,
            "branch_id": branch_id,
            "activity_log_id": entry.id,
            "device_id": device_id,
            "metric_type": record.metric_type,
            "recorded_date": record.recorded_date,
            "value": record.value,
            "unit": unit,
            "recording_method": record.recording_method,
            "source_name": record.source_name,
            "source_id": record.source_id,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "source_metadata": record.metadata,
            "synced_at": datetime.now(timezone.utc),
        }
        if audit is None:
            audit = HealthActivitySync(
                user_id=user_id,
                provider=provider,
                external_id=record.record_id,
                **values,
            )
            session.add(audit)
        else:
            for key, value in values.items():
                setattr(audit, key, value)

        await session.flush()
        if record.metric_type == "run" and record.recording_method == "active":
            daily_run = await session.scalar(
                select(func.coalesce(func.sum(HealthActivitySync.value), 0)).where(
                    HealthActivitySync.organization_id == organization_id,
                    HealthActivitySync.user_id == user_id,
                    HealthActivitySync.metric_type == "run",
                    HealthActivitySync.recorded_date == record.recorded_date,
                    HealthActivitySync.recording_method == "active",
                )
            )
            entry, updated = await submit_activity(
                session=session,
                organization_id=organization_id,
                branch_id=branch_id,
                user_id=user_id,
                metric_type="run",
                value=daily_run,
                unit=unit,
                source=provider,
                entry_date=record.recorded_date,
                is_verified=True,
            )

        entries.append(entry)
        challenge_ids.update(updated)

    await session.flush()
    return entries, sorted(challenge_ids)
