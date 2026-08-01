"""
app/jobs/wellbeing/window_lifecycle.py

open_survey_windows — for every org with an active WellbeingSurveyConfig,
opens a new WellbeingSurveyWindow when the previous one has ended (or
immediately if the org has never had one), and notifies every active
member (including admins) that the survey is open.

close_survey_windows — closes any window whose window_end has passed,
then triggers compute_pulse_snapshots for it (see snapshot job).
"""



from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from app.jobs.shared import get_job_session
from app.models import User
from app.models.wellbeing_survey import WellbeingSurveyConfig, WellbeingSurveyWindow
from app.services.notification import create_notification
from app.services.wellbeing_survey._helpers import compute_window_range

logger = logging.getLogger(__name__)


async def open_survey_windows(ctx: dict[str, Any]) -> None:
    today = date.today()
    opened = 0
    notified = 0

    async with get_job_session() as session:
        configs_result = await session.execute(
            select(WellbeingSurveyConfig).where(WellbeingSurveyConfig.is_active.is_(True))
        )
        configs = configs_result.scalars().all()

        for config in configs:
            latest_result = await session.execute(
                select(WellbeingSurveyWindow)
                .where(WellbeingSurveyWindow.organization_id == config.organization_id)
                .order_by(WellbeingSurveyWindow.window_start.desc())
                .limit(1)
            )
            latest = latest_result.scalar_one_or_none()

            needs_new_window = latest is None or (latest.is_closed and latest.window_end < today)
            if not needs_new_window:
                continue

            start = today if latest is None else latest.window_end + timedelta(days=1)
            if start > today:
                continue

            window_start, window_end = compute_window_range(cadence=config.cadence, start=start)
            window = WellbeingSurveyWindow(
                organization_id=config.organization_id, window_start=window_start, window_end=window_end,
            )
            session.add(window)
            await session.flush()
            opened += 1

            users_result = await session.execute(
                select(User.id).where(User.organization_id == config.organization_id, User.status == "active")
            )
            for (user_id,) in users_result.all():
                await create_notification(
                    session=session, user_id=user_id, organization_id=config.organization_id,
                    notification_type="announcement",
                    body="A new wellbeing survey is open — your responses help us support the team better.",
                    reference_id=window.id, reference_type="survey",
                )
                notified += 1

        await session.commit()

    logger.info("Survey windows opened", extra={"windows_opened": opened, "notifications_sent": notified})


async def close_survey_windows(ctx: dict[str, Any]) -> None:
    from app.jobs.wellbeing.pulse_snapshot import compute_pulse_snapshots

    today = date.today()
    closed = 0
    window_ids: list[str] = []

    async with get_job_session() as session:
        result = await session.execute(
            select(WellbeingSurveyWindow).where(
                WellbeingSurveyWindow.is_closed.is_(False), WellbeingSurveyWindow.window_end < today,
            )
        )
        windows = result.scalars().all()
        for window in windows:
            window.is_closed = True
            closed += 1
            window_ids.append(window.id)

        await session.commit()

    logger.info("Survey windows closed", extra={"windows_closed": closed})

    for window_id in window_ids:
        await compute_pulse_snapshots(ctx={}, window_id=window_id)