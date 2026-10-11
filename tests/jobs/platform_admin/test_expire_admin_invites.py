"""Job tests for app/jobs/platform_admin/expire_admin_invites.py."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import INVITE_TTL_DAYS
from app.jobs.platform_admin import expire_admin_invites as job_module
from tests.conftest import make_admin_user


def use_test_session(monkeypatch, db_session: AsyncSession) -> None:
    @asynccontextmanager
    async def test_job_session():
        yield db_session

    monkeypatch.setattr(job_module, "get_job_session", test_job_session)


@pytest.mark.asyncio
async def test_invite_older_than_ttl_is_disabled(db_session: AsyncSession, monkeypatch):
    stale = make_admin_user(
        email="stale@wellstaq.test", status="invited",
        invited_at=datetime.now(timezone.utc) - timedelta(days=INVITE_TTL_DAYS + 1),
    )
    db_session.add(stale)
    await db_session.flush()
    use_test_session(monkeypatch, db_session)

    await job_module.expire_admin_invites({})

    await db_session.refresh(stale)
    assert stale.status == "disabled"


@pytest.mark.asyncio
async def test_invite_within_ttl_is_left_alone(db_session: AsyncSession, monkeypatch):
    fresh = make_admin_user(
        email="fresh@wellstaq.test", status="invited",
        invited_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(fresh)
    await db_session.flush()
    use_test_session(monkeypatch, db_session)

    await job_module.expire_admin_invites({})

    await db_session.refresh(fresh)
    assert fresh.status == "invited"


@pytest.mark.asyncio
async def test_active_admins_are_never_touched(db_session: AsyncSession, monkeypatch):
    """An active admin's invited_at is irrelevant (already cleared by
    reset_password in practice), but this guards the query's status
    filter regardless of what invited_at happens to hold."""
    active = make_admin_user(
        email="active@wellstaq.test", status="active",
        invited_at=datetime.now(timezone.utc) - timedelta(days=INVITE_TTL_DAYS + 30),
    )
    db_session.add(active)
    await db_session.flush()
    use_test_session(monkeypatch, db_session)

    await job_module.expire_admin_invites({})

    await db_session.refresh(active)
    assert active.status == "active"


@pytest.mark.asyncio
async def test_invite_with_no_invited_at_is_left_alone(db_session: AsyncSession, monkeypatch):
    """Defensive: a row with invited_at never set (shouldn't happen via
    invite_admin, but the job must not crash or wrongly sweep it)."""
    undated = make_admin_user(email="undated@wellstaq.test", status="invited", invited_at=None)
    db_session.add(undated)
    await db_session.flush()
    use_test_session(monkeypatch, db_session)

    await job_module.expire_admin_invites({})

    await db_session.refresh(undated)
    assert undated.status == "invited"
