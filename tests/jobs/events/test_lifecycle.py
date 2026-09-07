"""Event lifecycle job tests."""

from contextlib import asynccontextmanager
from datetime import date, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.events import lifecycle
from app.models import Event
from tests.conftest import make_org_with_owner


def make_event(organization_id: str, branch_id: str, **overrides) -> Event:
    defaults = dict(
        title="Morning Walk",
        start_date=date.today(),
        time=time(8, 0),
        status="scheduled",
        organization_id=organization_id,
        branch_id=branch_id,
    )
    return Event(**{**defaults, **overrides})


def use_test_session(monkeypatch, db_session: AsyncSession) -> None:
    @asynccontextmanager
    async def test_job_session():
        yield db_session

    monkeypatch.setattr(lifecycle, "get_job_session", test_job_session)


class TestActivateScheduledEvents:
    @pytest.mark.asyncio
    async def test_due_event_becomes_ongoing(self, db_session: AsyncSession, monkeypatch):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        event = make_event(org.id, owner.branch_id, start_date=date.today() - timedelta(days=1))
        db_session.add(event)
        await db_session.flush()
        use_test_session(monkeypatch, db_session)

        await lifecycle.activate_scheduled_events({})

        assert event.status == "ongoing"

    @pytest.mark.asyncio
    async def test_future_event_remains_scheduled(self, db_session: AsyncSession, monkeypatch):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        event = make_event(org.id, owner.branch_id, start_date=date.today() + timedelta(days=1))
        db_session.add(event)
        await db_session.flush()
        use_test_session(monkeypatch, db_session)

        await lifecycle.activate_scheduled_events({})

        assert event.status == "scheduled"

    @pytest.mark.asyncio
    async def test_cancelled_event_is_not_reactivated(self, db_session: AsyncSession, monkeypatch):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        event = make_event(org.id, owner.branch_id, status="cancelled")
        db_session.add(event)
        await db_session.flush()
        use_test_session(monkeypatch, db_session)

        await lifecycle.activate_scheduled_events({})

        assert event.status == "cancelled"
