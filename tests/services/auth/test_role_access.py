"""Role-derived access regressions, using real ORM rows and signed tokens.

Coverage: org/branch slot independence, replacement/revocation, legacy grants,
member defaults, session revocation, and org-wide precedence.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.auth.dependencies import get_current_user
from app.core.constants import UserStatus
from app.models import Permission, Role, RolePermission, UserPermission, UserRoleAssignment, UserDevice
from app.routers.roles import _assign_role_slot
from app.services.auth.token_service import load_user_permissions, issue_tokens
from tests.conftest import make_org_with_owner


async def _role(session, org, name, branch_id=None, permission_name="overview"):
    role = Role(name=name, organization_id=org.id, branch_id=branch_id, is_system=False)
    session.add(role)
    await session.flush()
    if permission_name:
        permission = (await session.execute(select(Permission).where(Permission.name == permission_name))).scalar_one_or_none()
        if permission is None:
            permission = Permission(name=permission_name, category="dashboard", scope="both")
            session.add(permission)
            await session.flush()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id, scope="branch" if branch_id else "org"))
        await session.flush()
    return role


async def _grants(session, org, user):
    return {(p.name, p.branch_id) for p in await load_user_permissions(session, user.id, org.id)}


class TestRoleDerivedAccess:
    async def test_member_starts_without_permissions(self, db_session):
        org, user = await make_org_with_owner(db_session, user_kwargs={"role_name": "member"})
        assert await _grants(db_session, org, user) == set()

    async def test_legacy_direct_grant_does_not_restore_access(self, db_session):
        org, user = await make_org_with_owner(db_session, user_kwargs={"role_name": "member"})
        permission = Permission(name="overview", category="dashboard", scope="both")
        db_session.add(permission)
        await db_session.flush()
        db_session.add(UserPermission(user_id=user.id, organization_id=org.id, permission_id=permission.id, branch_id=None))
        await db_session.flush()
        assert await _grants(db_session, org, user) == set()

    async def test_custom_role_overview_is_effective(self, db_session):
        org, user = await make_org_with_owner(db_session)
        role = await _role(db_session, org, "dashboard-viewer")
        await _assign_role_slot(db_session, target=user, role=role)
        await db_session.flush()
        assert await _grants(db_session, org, user) == {("overview", None)}

    async def test_branch_role_replacement_removes_old_access(self, db_session):
        org, user = await make_org_with_owner(db_session)
        role = await _role(db_session, org, "local-viewer", user.branch_id)
        member = await _role(db_session, org, "member", user.branch_id, permission_name=None)
        await _assign_role_slot(db_session, target=user, role=role)
        assert await _grants(db_session, org, user) == {("overview", user.branch_id)}
        await _assign_role_slot(db_session, target=user, role=member)
        assert await _grants(db_session, org, user) == set()
        assignments = (await db_session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))).scalars().all()
        assert [a.role_id for a in assignments] == [member.id]

    async def test_org_replacement_keeps_branch_access_only(self, db_session):
        org, user = await make_org_with_owner(db_session)
        org_role = await _role(db_session, org, "org-viewer")
        branch_role = await _role(db_session, org, "branch-viewer", user.branch_id, "event.create")
        member = await _role(db_session, org, "member", permission_name=None)
        await _assign_role_slot(db_session, target=user, role=org_role)
        await _assign_role_slot(db_session, target=user, role=branch_role)
        await _assign_role_slot(db_session, target=user, role=member)
        await db_session.flush()
        assert await _grants(db_session, org, user) == {("event.create", user.branch_id)}

    async def test_org_scope_overrides_same_branch_permission(self, db_session):
        org, user = await make_org_with_owner(db_session)
        org_role = await _role(db_session, org, "org-viewer")
        branch_role = await _role(db_session, org, "branch-viewer", user.branch_id)
        await _assign_role_slot(db_session, target=user, role=org_role)
        await _assign_role_slot(db_session, target=user, role=branch_role)
        assert await _grants(db_session, org, user) == {("overview", None)}

    async def test_deleting_assignment_removes_branch_access_immediately(self, db_session):
        org, user = await make_org_with_owner(db_session)
        role = await _role(db_session, org, "branch-viewer", user.branch_id)
        await _assign_role_slot(db_session, target=user, role=role)
        assert await _grants(db_session, org, user)
        await db_session.execute(delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))
        assert await _grants(db_session, org, user) == set()

    @pytest.mark.parametrize("branch_scoped", [False, True])
    async def test_role_change_revokes_existing_session(self, db_session, branch_scoped):
        org, user = await make_org_with_owner(db_session, user_kwargs={"status": UserStatus.ACTIVE})
        tokens = await issue_tokens(db_session, user, "Browser", "test-device", "127.0.0.1")
        role = await _role(db_session, org, "new-viewer", user.branch_id if branch_scoped else None)
        await _assign_role_slot(db_session, target=user, role=role)
        await db_session.flush()
        device = (await db_session.execute(select(UserDevice).where(UserDevice.user_id == user.id))).scalar_one()
        assert device.revoked is True
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=SimpleNamespace(credentials=tokens["access_token"]), db=db_session)
        assert exc.value.status_code == 401
