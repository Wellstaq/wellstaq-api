"""Service tests for app/services/platform_admin/admin_team_service.py."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.platform_admin import admin_team_service
from tests.conftest import make_admin_user


async def _persisted_admin(db_session, **overrides):
    admin = make_admin_user(**overrides)
    db_session.add(admin)
    await db_session.flush()
    return admin


@pytest.mark.asyncio
async def test_invite_admin_creates_invited_row(db_session):
    inviter = await _persisted_admin(db_session, email="owner@wellstaq.test")
    admin = await admin_team_service.invite_admin(session=db_session, email="new@wellstaq.test", role="admin", invited_by=inviter.id)
    assert admin.status == "invited"
    assert admin.role == "admin"
    assert admin.hashed_password is None
    assert admin.invited_by == inviter.id


@pytest.mark.asyncio
async def test_invite_admin_rejects_duplicate_email(db_session):
    inviter = await _persisted_admin(db_session, email="owner@wellstaq.test")
    await _persisted_admin(db_session, email="taken@wellstaq.test")
    with pytest.raises(HTTPException) as exc:
        await admin_team_service.invite_admin(session=db_session, email="taken@wellstaq.test", role="support", invited_by=inviter.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_invite_admin_rejects_invalid_role(db_session):
    inviter = await _persisted_admin(db_session, email="owner@wellstaq.test")
    with pytest.raises(HTTPException) as exc:
        await admin_team_service.invite_admin(session=db_session, email="new@wellstaq.test", role="owner", invited_by=inviter.id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_admin_role_changes_role(db_session):
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test")
    target = await _persisted_admin(db_session, email="target@wellstaq.test", role="support")
    updated = await admin_team_service.update_admin_role(session=db_session, admin_id=target.id, new_role="admin", acting_admin_id=acting.id)
    assert updated.role == "admin"


@pytest.mark.asyncio
async def test_cannot_change_own_role(db_session):
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test")
    with pytest.raises(HTTPException) as exc:
        await admin_team_service.update_admin_role(session=db_session, admin_id=acting.id, new_role="support", acting_admin_id=acting.id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cannot_demote_the_last_super_admin(db_session):
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test", role="admin")
    last_super = await _persisted_admin(db_session, email="lastsuper@wellstaq.test", role="super_admin")
    with pytest.raises(HTTPException) as exc:
        await admin_team_service.update_admin_role(session=db_session, admin_id=last_super.id, new_role="admin", acting_admin_id=acting.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_can_demote_a_super_admin_when_another_remains(db_session):
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test", role="super_admin")
    other_super = await _persisted_admin(db_session, email="other@wellstaq.test", role="super_admin")
    updated = await admin_team_service.update_admin_role(session=db_session, admin_id=other_super.id, new_role="admin", acting_admin_id=acting.id)
    assert updated.role == "admin"


@pytest.mark.asyncio
async def test_cannot_remove_self(db_session):
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test")
    with pytest.raises(HTTPException) as exc:
        await admin_team_service.remove_admin(session=db_session, admin_id=acting.id, acting_admin_id=acting.id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cannot_remove_the_last_super_admin(db_session):
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test", role="admin")
    last_super = await _persisted_admin(db_session, email="lastsuper@wellstaq.test", role="super_admin")
    with pytest.raises(HTTPException) as exc:
        await admin_team_service.remove_admin(session=db_session, admin_id=last_super.id, acting_admin_id=acting.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_remove_admin_disables_rather_than_deletes(db_session):
    """Soft-revoke, not a hard delete — keeps audit-log actor references
    intact (see AdminUser model docstring)."""
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test")
    target = await _persisted_admin(db_session, email="target@wellstaq.test", role="support")
    await admin_team_service.remove_admin(session=db_session, admin_id=target.id, acting_admin_id=acting.id)
    await db_session.refresh(target)
    assert target.status == "disabled"


@pytest.mark.asyncio
async def test_list_admins_paginates_and_filters_by_q(db_session):
    await _persisted_admin(db_session, email="alpha@wellstaq.test", first_name="Alpha")
    await _persisted_admin(db_session, email="beta@wellstaq.test", first_name="Beta")
    rows, total = await admin_team_service.list_admins(session=db_session, q="Alpha", offset=0, limit=20)
    assert total == 1
    assert rows[0].email == "alpha@wellstaq.test"


@pytest.mark.asyncio
async def test_list_admins_excludes_removed_admins(db_session):
    """A removed admin (or a cancelled invite) is soft-disabled, not
    deleted, but shouldn't linger in the team roster after reload."""
    acting = await _persisted_admin(db_session, email="acting@wellstaq.test")
    target = await _persisted_admin(db_session, email="target@wellstaq.test", role="support")
    await admin_team_service.remove_admin(session=db_session, admin_id=target.id, acting_admin_id=acting.id)

    rows, total = await admin_team_service.list_admins(session=db_session, q=None, offset=0, limit=20)

    assert target.id not in {row.id for row in rows}
    assert total == 1
