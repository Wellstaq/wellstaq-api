import pytest
from fastapi import HTTPException

from app.auth.dependencies import CurrentUser
from app.models import Role
from app.routers.roles import _validate_role_reassignment, revoke_member_role
from tests.conftest import make_org_with_owner


@pytest.mark.asyncio
async def test_super_admin_role_cannot_be_reassigned(db_session):
    org, owner = await make_org_with_owner(
        db_session, user_kwargs={"role_name": "super_admin", "email": "protected-owner@example.com"},
    )
    replacement = Role(name="member", organization_id=org.id, branch_id=None, is_system=False)
    db_session.add(replacement)
    await db_session.flush()
    actor = CurrentUser(user_id="another-user", role="super_admin", organization_id=org.id, branch_id=owner.branch_id)

    with pytest.raises(HTTPException) as exc:
        await _validate_role_reassignment(db_session, actor, owner, replacement)
    assert exc.value.status_code == 403
    assert "Super Admin" in exc.value.detail


@pytest.mark.asyncio
async def test_super_admin_role_cannot_be_revoked(db_session):
    org, owner = await make_org_with_owner(
        db_session, user_kwargs={"role_name": "super_admin", "email": "protected-revoke@example.com"},
    )
    actor = CurrentUser(user_id="another-user", role="super_admin", organization_id=org.id, branch_id=owner.branch_id)

    with pytest.raises(HTTPException) as exc:
        await revoke_member_role(org.id, owner.id, None, db_session, actor)
    assert exc.value.status_code == 403
    assert "Super Admin" in exc.value.detail
