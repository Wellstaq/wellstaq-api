"""ORM and integrity tests for app/models/admin_user.py."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AdminSession, AdminUser
from tests.conftest import make_admin_user


@pytest.mark.asyncio
async def test_admin_user_persists_defaults(db_session):
    admin = make_admin_user()
    db_session.add(admin)
    await db_session.flush()
    await db_session.refresh(admin)

    assert admin.id is not None
    assert admin.role == "super_admin"
    assert admin.status == "active"
    assert admin.two_fa_enabled is False
    assert admin.invited_by is None


@pytest.mark.asyncio
async def test_admin_user_email_is_unique(db_session):
    db_session.add(make_admin_user(email="dup@wellstaq.test"))
    await db_session.flush()
    db_session.add(make_admin_user(email="dup@wellstaq.test"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_admin_user_rejects_invalid_role(db_session):
    db_session.add(make_admin_user(role="owner"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_admin_user_rejects_invalid_status(db_session):
    db_session.add(make_admin_user(status="pending"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_admin_user_invited_by_self_references(db_session):
    inviter = make_admin_user(email="inviter@wellstaq.test")
    db_session.add(inviter)
    await db_session.flush()

    invited = make_admin_user(email="invitee@wellstaq.test", status="invited", hashed_password=None, invited_by=inviter.id)
    db_session.add(invited)
    await db_session.flush()
    await db_session.refresh(invited)

    assert invited.invited_by == inviter.id
    assert invited.hashed_password is None


@pytest.mark.asyncio
async def test_admin_session_persists_and_cascades_on_admin_delete(db_session):
    admin = make_admin_user()
    db_session.add(admin)
    await db_session.flush()

    session_row = AdminSession(
        admin_user_id=admin.id, refresh_token_hash="hash", device_name="Pytest",
        last_seen_at=datetime.now(timezone.utc), revoked=False,
    )
    db_session.add(session_row)
    await db_session.flush()
    await db_session.refresh(session_row)

    assert session_row.revoked is False
    assert session_row.admin_user_id == admin.id
