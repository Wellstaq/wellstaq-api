"""ORM and integrity tests for app/models/audit_entry.py."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuditEntry
from tests.conftest import make_admin_user


@pytest.mark.asyncio
async def test_audit_entry_persists(db_session):
    admin = make_admin_user()
    db_session.add(admin)
    await db_session.flush()

    entry = AuditEntry(
        actor_admin_id=admin.id, action="organization.suspended", target_type="organization",
        target_id="some-org-id", target_label="Acme Corp", reason="Unpaid invoice.",
    )
    db_session.add(entry)
    await db_session.flush()
    await db_session.refresh(entry)

    assert entry.id is not None
    assert entry.created_at is not None
    assert entry.reason == "Unpaid invoice."


@pytest.mark.asyncio
async def test_audit_entry_survives_actor_deletion(db_session):
    """actor_admin_id is SET NULL, not CASCADE — deleting an admin must
    never erase the historical record of what they did."""
    admin = make_admin_user()
    db_session.add(admin)
    await db_session.flush()

    entry = AuditEntry(
        actor_admin_id=admin.id, action="admin.removed", target_type="admin",
        target_id="some-admin-id", target_label="Former Admin",
    )
    db_session.add(entry)
    await db_session.flush()

    await db_session.delete(admin)
    await db_session.flush()
    await db_session.refresh(entry)

    assert entry.actor_admin_id is None
    assert entry.action == "admin.removed"


@pytest.mark.asyncio
async def test_audit_entry_rejects_invalid_target_type(db_session):
    db_session.add(AuditEntry(
        actor_admin_id=None, action="whatever", target_type="not_a_real_type",
        target_id="x", target_label="x",
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()
