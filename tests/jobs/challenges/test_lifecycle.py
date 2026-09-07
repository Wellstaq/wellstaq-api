"""Challenge lifecycle job tests."""

from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.challenges import lifecycle
from app.models import Challenge
from tests.conftest import make_org_with_owner


def make_challenge(organization_id: str, **overrides) -> Challenge:
    defaults = dict(
        name="Daily Steps",
        status="upcoming",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=7),
        metric_type="steps",
        target_type="cumulative",
        target_value=10000,
        organization_id=organization_id,
    )
    return Challenge(**{**defaults, **overrides})


def use_test_session(monkeypatch, db_session: AsyncSession) -> None:
    @asynccontextmanager
    async def test_job_session():
        yield db_session

    monkeypatch.setattr(lifecycle, "get_job_session", test_job_session)


class TestActivateAndCloseChallenges:
    @pytest.mark.asyncio
    async def test_due_upcoming_challenge_becomes_active(self, db_session: AsyncSession, monkeypatch):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        challenge = make_challenge(org.id)
        db_session.add(challenge)
        await db_session.flush()
        use_test_session(monkeypatch, db_session)

        await lifecycle.activate_and_close_challenges({})

        assert challenge.status == "active"

    @pytest.mark.asyncio
    async def test_ended_active_challenge_becomes_completed(self, db_session: AsyncSession, monkeypatch):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        challenge = make_challenge(
            org.id,
            status="active",
            start_date=date.today() - timedelta(days=2),
            end_date=date.today() - timedelta(days=1),
        )
        db_session.add(challenge)
        await db_session.flush()
        use_test_session(monkeypatch, db_session)

        await lifecycle.activate_and_close_challenges({})

        assert challenge.status == "completed"

    @pytest.mark.asyncio
    async def test_challenge_remains_active_through_its_end_date(self, db_session: AsyncSession, monkeypatch):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        challenge = make_challenge(org.id, status="active", end_date=date.today())
        db_session.add(challenge)
        await db_session.flush()
        use_test_session(monkeypatch, db_session)

        await lifecycle.activate_and_close_challenges({})

        assert challenge.status == "active"
