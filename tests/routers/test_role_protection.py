import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.dependencies import CurrentUser
from app.models import Branch, Permission, Role, RolePermission, UserRoleAssignment
from app.routers.roles import (
    RoleCreateRequest,
    RolePermissionsUpdateRequest,
    _assign_role_slot,
    _validate_role_reassignment,
    create_organization_role,
    revoke_member_role,
    update_role_permissions,
)
from app.services.organization.org_directory import get_organization_members
from tests.conftest import make_org_with_owner, make_user_with_role


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


@pytest.mark.asyncio
async def test_branch_manager_can_manage_multiple_branches_without_moving_home_branch(db_session):
    org, owner = await make_org_with_owner(
        db_session, with_trial=False,
        org_kwargs={"slug": "multi-manager", "business_name": "Multi Manager"},
        user_kwargs={"role_name": "super_admin", "email": "multi-owner@example.com"},
    )
    second = Branch(organization_id=org.id, name="Second")
    third = Branch(organization_id=org.id, name="Third")
    db_session.add_all([second, third])
    await db_session.flush()
    manager = await make_user_with_role(
        db_session, organization_id=org.id, branch_id=owner.branch_id,
        email="multi-manager@example.com",
    )
    db_session.add(manager)
    await db_session.flush()
    roles = [Role(name="branch_manager", organization_id=org.id, branch_id=branch.id, is_system=False)
             for branch in (second, third)]
    db_session.add_all(roles)
    await db_session.flush()

    for role in roles:
        await _assign_role_slot(db_session, target=manager, role=role)

    assignments = (await db_session.execute(
        select(UserRoleAssignment).where(UserRoleAssignment.user_id == manager.id)
    )).scalars().all()
    assert {assignment.branch_id for assignment in assignments} == {second.id, third.id}
    assert manager.branch_id == owner.branch_id


@pytest.mark.asyncio
async def test_org_directory_shows_both_role_levels_while_branch_view_is_local(db_session):
    org, owner = await make_org_with_owner(
        db_session, with_trial=False,
        org_kwargs={"slug": "role-directory", "business_name": "Role Directory"},
        user_kwargs={"role_name": "super_admin", "email": "directory-owner@example.com"},
    )
    managed = Branch(organization_id=org.id, name="Managed Elsewhere")
    db_session.add(managed)
    await db_session.flush()
    manager_role = Role(name="branch_manager", organization_id=org.id, branch_id=managed.id, is_system=False)
    db_session.add(manager_role)
    await db_session.flush()
    db_session.add(UserRoleAssignment(
        user_id=owner.id, role_id=manager_role.id, organization_id=org.id, branch_id=managed.id,
    ))
    await db_session.flush()

    org_members, _ = await get_organization_members(session=db_session, organization_id=org.id)
    owner_org = next(item for item in org_members if item["id"] == owner.id)
    assert owner_org["organization_role_name"] == "super_admin"
    assert owner_org["branch_roles"] == [{
        "branch_id": managed.id, "role_id": manager_role.id, "role_name": "branch_manager",
    }]

    branch_members, _ = await get_organization_members(
        session=db_session, organization_id=org.id, branch_id=managed.id,
    )
    owner_branch = next(item for item in branch_members if item["id"] == owner.id)
    assert owner_branch["role_name"] == "branch_manager"
    assert owner_branch["organization_role_name"] is None
    assert len(owner_branch["branch_roles"]) == 1


@pytest.mark.asyncio
async def test_org_role_always_keeps_overview_permission(db_session):
    org, owner = await make_org_with_owner(
        db_session, with_trial=False,
        org_kwargs={"slug": "mandatory-overview", "business_name": "Mandatory Overview"},
        user_kwargs={"role_name": "super_admin", "email": "overview-owner@example.com"},
    )
    overview = Permission(name="overview", scope="both")
    db_session.add(overview)
    await db_session.flush()
    actor = CurrentUser(user_id=owner.id, role="super_admin", organization_id=org.id, branch_id=owner.branch_id)

    created = await create_organization_role(org.id, RoleCreateRequest(name="Org Analyst"), db_session, actor)
    role = (await db_session.execute(select(Role).where(Role.id == created.id))).scalar_one()
    grants = (await db_session.execute(
        select(Permission.name).join(RolePermission).where(RolePermission.role_id == role.id)
    )).scalars().all()
    assert grants == ["overview"]

    updated = await update_role_permissions(
        org.id, role.id, RolePermissionsUpdateRequest(permission_names=[]), db_session, actor,
    )
    assert updated.permissions == ["overview"]
