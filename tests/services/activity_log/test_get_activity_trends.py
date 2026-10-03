"""
Tests for app/services/activity_log/get_activity_trends.py.

Daily, weekly, and monthly personal trends are tested with real rows,
normalization, ordering, aggregation, user isolation, and limits.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog
from app.services.activity_log.get_activity_trends import get_activity_trend
from tests.conftest import make_org_with_owner, make_user


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


class TestDailyActivityTrend:

    @pytest.mark.asyncio
    async def test_returns_oldest_to_newest_and_normalizes_metric(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        await _add_log(db_session, org, user, day=date(2026, 9, 3), value=300)
        await _add_log(db_session, org, user, day=date(2026, 9, 1), value=100)

        result = await get_activity_trend(
            session=db_session,
            user_id=user.id,
            metric_type=" STEPS ",
            granularity="daily",
        )

        assert [(day, float(value)) for day, value in result] == [
            (date(2026, 9, 1), 100),
            (date(2026, 9, 3), 300),
        ]

    @pytest.mark.asyncio
    async def test_limit_keeps_most_recent_rows(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        for day in range(1, 5):
            await _add_log(
                db_session, org, user, day=date(2026, 9, day), value=day
            )

        result = await get_activity_trend(
            session=db_session,
            user_id=user.id,
            metric_type="steps",
            granularity="daily",
            limit=2,
        )

        assert [day for day, _ in result] == [date(2026, 9, 3), date(2026, 9, 4)]

    @pytest.mark.asyncio
    async def test_excludes_other_users_and_metrics(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        other = make_user(
            org.id, email="trend-other@example.com", branch_id=user.branch_id
        )
        db_session.add(other)
        await db_session.flush()
        await _add_log(db_session, org, user, day=date(2026, 9, 1), value=100)
        await _add_log(
            db_session, org, user, day=date(2026, 9, 1), value=5, metric_type="distance"
        )
        await _add_log(db_session, org, other, day=date(2026, 9, 1), value=999)

        result = await get_activity_trend(
            session=db_session, user_id=user.id, metric_type="steps"
        )

        assert len(result) == 1
        assert float(result[0][1]) == 100


class TestGroupedActivityTrend:

    @pytest.mark.asyncio
    async def test_weekly_sums_rows_by_monday(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        await _add_log(db_session, org, user, day=date(2026, 9, 7), value=100)
        await _add_log(db_session, org, user, day=date(2026, 9, 9), value=250)
        await _add_log(db_session, org, user, day=date(2026, 9, 14), value=400)

        result = await get_activity_trend(
            session=db_session,
            user_id=user.id,
            metric_type="steps",
            granularity="weekly",
        )

        assert [(day, float(value)) for day, value in result] == [
            (date(2026, 9, 7), 350),
            (date(2026, 9, 14), 400),
        ]

    @pytest.mark.asyncio
    async def test_monthly_sums_rows_by_first_day(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        await _add_log(db_session, org, user, day=date(2026, 8, 10), value=100)
        await _add_log(db_session, org, user, day=date(2026, 8, 20), value=200)
        await _add_log(db_session, org, user, day=date(2026, 9, 1), value=400)

        result = await get_activity_trend(
            session=db_session,
            user_id=user.id,
            metric_type="steps",
            granularity="monthly",
        )

        assert [(day, float(value)) for day, value in result] == [
            (date(2026, 8, 1), 300),
            (date(2026, 9, 1), 400),
        ]

    @pytest.mark.asyncio
    async def test_invalid_granularity_raises(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        await _add_log(db_session, org, user, day=date(2026, 9, 1), value=100)

        with pytest.raises(ValueError, match="Invalid granularity"):
            await get_activity_trend(
                session=db_session,
                user_id=user.id,
                metric_type="steps",
                granularity="yearly",
            )
