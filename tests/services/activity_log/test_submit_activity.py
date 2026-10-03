"""
Tests for app/services/activity_log/submit_activity.py.

Tests cover normalized daily upserts and the active-challenge fan-out using
real persisted activity, challenge, participant, and progress rows.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, Challenge, ChallengeParticipant, ChallengeProgressEntry
from app.services.activity_log.submit_activity import submit_activity
from tests.conftest import make_org_with_owner


async def _submit(db_session: AsyncSession, org, user, **overrides):
    values = {
        "metric_type": " Steps ",
        "value": 5000,
        "unit": None,
        "source": "manual",
    }
    return await submit_activity(
        session=db_session,
        organization_id=org.id,
        branch_id=user.branch_id,
        user_id=user.id,
        **{**values, **overrides},
    )


async def _make_challenge(
    db_session: AsyncSession,
    org,
    user,
    *,
    name: str,
    metric_type: str = "steps",
    status: str = "active",
) -> Challenge:
    challenge = Challenge(
        organization_id=org.id,
        branch_id=user.branch_id,
        name=name,
        start_date=date.today() - timedelta(days=1),
        end_date=date.today() + timedelta(days=7),
        status=status,
        metric_type=metric_type,
        target_type="cumulative",
        target_value=100_000,
        created_by=user.id,
    )
    db_session.add(challenge)
    await db_session.flush()
    db_session.add(ChallengeParticipant(
        challenge_id=challenge.id,
        user_id=user.id,
        organization_id=org.id,
    ))
    await db_session.flush()
    return challenge


class TestSubmitActivityDailyUpsert:

    @pytest.mark.asyncio
    async def test_creates_normalized_activity_for_user_branch(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session, with_trial=False)

        entry, updated = await _submit(db_session, org, user)

        assert entry.organization_id == org.id
        assert entry.branch_id == user.branch_id
        assert entry.user_id == user.id
        assert entry.metric_type == "steps"
        assert float(entry.value) == 5000
        assert entry.date == date.today()
        assert updated == []

    @pytest.mark.asyncio
    async def test_second_submission_updates_same_row_instead_of_duplicating(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        first, _ = await _submit(db_session, org, user, value=1000)

        second, _ = await _submit(db_session, org, user, value=2500, unit="steps")

        assert second.id == first.id
        assert float(second.value) == 2500
        assert second.unit == "steps"
        count = (
            await db_session.execute(
                select(func.count()).select_from(ActivityLog).where(
                    ActivityLog.user_id == user.id,
                    ActivityLog.metric_type == "steps",
                )
            )
        ).scalar_one()
        assert count == 1

    @pytest.mark.asyncio
    async def test_different_metric_creates_a_separate_daily_row(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        steps, _ = await _submit(db_session, org, user)
        distance, _ = await _submit(
            db_session, org, user, metric_type="distance", value=4.2, unit="km"
        )

        assert steps.id != distance.id
        assert distance.metric_type == "distance"


class TestSubmitActivityChallengeFanout:

    @pytest.mark.asyncio
    async def test_updates_only_matching_active_joined_challenges(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        matching = await _make_challenge(
            db_session, org, user, name="Matching Steps"
        )
        await _make_challenge(
            db_session, org, user, name="Wrong Metric", metric_type="distance"
        )
        await _make_challenge(
            db_session, org, user, name="Not Active", status="upcoming"
        )

        _, updated = await _submit(db_session, org, user, value=7200)

        assert updated == [matching.id]
        progress = (
            await db_session.execute(
                select(ChallengeProgressEntry).where(
                    ChallengeProgressEntry.challenge_id == matching.id,
                    ChallengeProgressEntry.user_id == user.id,
                )
            )
        ).scalar_one()
        assert float(progress.value) == 7200
        assert progress.source == "activity_log"

    @pytest.mark.asyncio
    async def test_resubmission_updates_existing_challenge_progress(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session, with_trial=False)
        challenge = await _make_challenge(
            db_session, org, user, name="Daily Sync"
        )
        await _submit(db_session, org, user, value=1000)

        await _submit(db_session, org, user, value=3000)

        rows = (
            await db_session.execute(
                select(ChallengeProgressEntry).where(
                    ChallengeProgressEntry.challenge_id == challenge.id,
                    ChallengeProgressEntry.user_id == user.id,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert float(rows[0].value) == 3000
