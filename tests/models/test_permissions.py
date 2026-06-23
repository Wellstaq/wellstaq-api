"""
Permission, Role, RolePermission, UserRole model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

What's tested:
  - Permission: persistence, name uniqueness, category, cascades
  - Role: system vs org-custom, CHECK constraint, name+org uniqueness,
    cascades on org delete
  - RolePermission: the join, uniqueness, cascades from both sides
  - UserRole: org-wide vs branch-scoped assignments, same role at both
    scopes simultaneously, duplicate prevention, cascade behaviour from
    org/user/role/branch deletion, the permission-check query pattern
    documented in the model, assigned_by audit trail and SET NULL on
    assigner deletion

SQLite NULL uniqueness caveat (documented in Role model docstring):
  A UNIQUE constraint on (name, organization_id) does NOT prevent two
  system roles from sharing the same name on SQLite, because SQLite
  treats two NULL values as distinct in a unique index. Tests that
  verify the service-layer responsibility for system-role name
  uniqueness are marked with a comment noting this limitation. The
  constraint DOES correctly block duplicate names within the same org.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, User,Permission, Role, RolePermission, UserRole
from tests.conftest import make_user, make_org_with_owner


# ── Factories ──────────────────────────────────────────────────────────────────

def make_permission(name: str = "post.create", **overrides) -> Permission:
    defaults = dict(description="Create a post", category="posts")
    return Permission(name=name, **{**defaults, **overrides})


def make_system_role(name: str = "Employee", **overrides) -> Role:
    """Platform default role — is_system=True, organization_id=NULL."""
    return Role(name=name, is_system=True, organization_id=None, **overrides)


def make_org_role(organization_id: str, name: str = "Wellness Lead", **overrides) -> Role:
    """Org-custom role — is_system=False, organization_id set."""
    return Role(name=name, is_system=False, organization_id=organization_id, **overrides)


def make_role_permission(role_id: str, permission_id: str) -> RolePermission:
    return RolePermission(role_id=role_id, permission_id=permission_id)


def make_user_role(
    user_id: str,
    role_id: str,
    organization_id: str,
    branch_id: str | None = None,
    assigned_by: str | None = None,
) -> UserRole:
    return UserRole(
        user_id=user_id,
        role_id=role_id,
        organization_id=organization_id,
        branch_id=branch_id,
        assigned_by=assigned_by,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
) -> tuple[User, Organization]:
    """Thin wrapper — preserves (user, org) return order."""
    org, user = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=slug),
        user_kwargs=dict(email=email, role_name="overall_manager"),
    )
    return user, org


async def setup_branch(db_session: AsyncSession, org_id: str, name: str = "Lagos Branch"):
    from app.models import Branch
    branch = Branch(organization_id=org_id, name=name)
    db_session.add(branch)
    await db_session.flush()
    return branch


# ── Permission ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_permission_persists(db_session: AsyncSession):
    perm = make_permission("post.create")
    db_session.add(perm)
    await db_session.flush()

    result = await db_session.execute(
        select(Permission).where(Permission.id == perm.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "post.create"
    assert fetched.description == "Create a post"
    assert fetched.category == "posts"


@pytest.mark.asyncio
async def test_permission_id_is_uuid_format(db_session: AsyncSession):
    perm = make_permission()
    db_session.add(perm)
    await db_session.flush()

    parts = perm.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_permission_timestamps_auto_set(db_session: AsyncSession):
    perm = make_permission()
    db_session.add(perm)
    await db_session.flush()

    assert perm.created_at is not None
    assert perm.updated_at is not None


@pytest.mark.asyncio
async def test_permission_name_is_unique(db_session: AsyncSession):
    """Two permissions cannot share the same name."""
    db_session.add(make_permission("post.delete"))
    await db_session.flush()

    db_session.add(make_permission("post.delete", description="Another one"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_permission_optional_fields_nullable(db_session: AsyncSession):
    perm = Permission(name="event.cancel")
    db_session.add(perm)
    await db_session.flush()

    assert perm.description is None
    assert perm.category is None


@pytest.mark.asyncio
async def test_multiple_permissions_different_categories(db_session: AsyncSession):
    db_session.add(make_permission("post.create", category="posts"))
    db_session.add(make_permission("event.create", category="events"))
    db_session.add(make_permission("wellbeing.view_team", category="wellbeing"))
    await db_session.flush()

    result = await db_session.execute(
        select(Permission).where(Permission.category == "posts")
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_permission_name_required(db_session: AsyncSession):
    perm = Permission(name=None, category="posts")
    db_session.add(perm)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Role — system roles ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_system_role_persists(db_session: AsyncSession):
    role = make_system_role("Super Admin")
    db_session.add(role)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id == role.id))
    fetched = result.scalar_one()
    assert fetched.name == "Super Admin"
    assert fetched.is_system is True
    assert fetched.organization_id is None


@pytest.mark.asyncio
async def test_system_role_id_is_uuid_format(db_session: AsyncSession):
    role = make_system_role()
    db_session.add(role)
    await db_session.flush()

    parts = role.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_system_role_timestamps_auto_set(db_session: AsyncSession):
    role = make_system_role()
    db_session.add(role)
    await db_session.flush()

    assert role.created_at is not None
    assert role.updated_at is not None


@pytest.mark.asyncio
async def test_system_role_check_constraint_blocks_org_id(db_session: AsyncSession):
    """is_system=True with organization_id set violates the CHECK constraint."""
    _, org = await setup_org(db_session)

    role = Role(name="Fake System Role", is_system=True, organization_id=org.id)
    db_session.add(role)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_multiple_system_roles_can_exist(db_session: AsyncSession):
    db_session.add(make_system_role("Super Admin"))
    db_session.add(make_system_role("Branch Manager"))
    db_session.add(make_system_role("Employee"))
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.is_system == True)
    )
    names = {r.name for r in result.scalars().all()}
    assert names == {"Super Admin", "Branch Manager", "Employee"}


# ── Role — org-custom roles ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_role_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    role = make_org_role(org.id, "Wellness Lead")
    db_session.add(role)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id == role.id))
    fetched = result.scalar_one()
    assert fetched.name == "Wellness Lead"
    assert fetched.is_system is False
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_org_role_check_constraint_blocks_null_org(db_session: AsyncSession):
    """is_system=False with organization_id=NULL violates the CHECK constraint."""
    role = Role(name="Custom Role", is_system=False, organization_id=None)
    db_session.add(role)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_role_name_unique_within_org(db_session: AsyncSession):
    """Two custom roles in the same org cannot share a name."""
    _, org = await setup_org(db_session)

    db_session.add(make_org_role(org.id, "Wellness Lead"))
    await db_session.flush()

    db_session.add(make_org_role(org.id, "Wellness Lead"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_role_name_allowed_in_different_orgs(db_session: AsyncSession):
    """Two different orgs can each have a role with the same name."""
    _, org_a = await setup_org(
        db_session, email="owner_a@example.com", slug="org-a"
    )
    _, org_b = await setup_org(
        db_session, email="owner_b@example.com", slug="org-b"
    )

    db_session.add(make_org_role(org_a.id, "Team Lead"))
    db_session.add(make_org_role(org_b.id, "Team Lead"))
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.name == "Team Lead", Role.is_system == False)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_org_delete_cascades_to_custom_roles(db_session: AsyncSession):
    """Deleting an org deletes its custom roles."""
    _, org = await setup_org(db_session)

    db_session.add(make_org_role(org.id, "Wellness Lead"))
    db_session.add(make_org_role(org.id, "Safety Officer"))
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.organization_id == org.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_org_delete_does_not_affect_system_roles(db_session: AsyncSession):
    """System roles survive org deletion — they belong to no org."""
    _, org = await setup_org(db_session)

    system_role = make_system_role("Employee")
    db_session.add(system_role)
    await db_session.flush()
    role_id = system_role.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id == role_id))
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_role_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    role = make_org_role(org.id, "Wellness Lead")
    db_session.add(role)
    await db_session.flush()
    await db_session.refresh(role, ["organization"])

    assert role.organization is not None
    assert role.organization.id == org.id


@pytest.mark.asyncio
async def test_organization_custom_roles_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    db_session.add(make_org_role(org.id, "Wellness Lead"))
    db_session.add(make_org_role(org.id, "Safety Officer"))
    await db_session.flush()
    await db_session.refresh(org, ["custom_roles"])

    assert len(org.custom_roles) == 2
    names = {r.name for r in org.custom_roles}
    assert names == {"Wellness Lead", "Safety Officer"}


# ── RolePermission ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_role_permission_persists(db_session: AsyncSession):
    role = make_system_role()
    perm = make_permission("post.create")
    db_session.add_all([role, perm])
    await db_session.flush()

    rp = make_role_permission(role.id, perm.id)
    db_session.add(rp)
    await db_session.flush()

    result = await db_session.execute(
        select(RolePermission).where(RolePermission.id == rp.id)
    )
    fetched = result.scalar_one()
    assert fetched.role_id == role.id
    assert fetched.permission_id == perm.id


@pytest.mark.asyncio
async def test_role_permission_duplicate_raises(db_session: AsyncSession):
    """A permission can only be granted to a role once."""
    role = make_system_role()
    perm = make_permission("post.create")
    db_session.add_all([role, perm])
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm.id))
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_permission_can_be_granted_to_multiple_roles(db_session: AsyncSession):
    """Different roles can each have the same permission."""
    role_a = make_system_role("Super Admin")
    role_b = make_system_role("Branch Manager")
    perm = make_permission("event.create")
    db_session.add_all([role_a, role_b, perm])
    await db_session.flush()

    db_session.add(make_role_permission(role_a.id, perm.id))
    db_session.add(make_role_permission(role_b.id, perm.id))
    await db_session.flush()

    result = await db_session.execute(
        select(RolePermission).where(RolePermission.permission_id == perm.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_role_can_have_multiple_permissions(db_session: AsyncSession):
    role = make_system_role("Super Admin")
    perm_a = make_permission("post.create")
    perm_b = make_permission("post.delete")
    perm_c = make_permission("event.create", category="events")
    db_session.add_all([role, perm_a, perm_b, perm_c])
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm_a.id))
    db_session.add(make_role_permission(role.id, perm_b.id))
    db_session.add(make_role_permission(role.id, perm_c.id))
    await db_session.flush()

    result = await db_session.execute(
        select(RolePermission).where(RolePermission.role_id == role.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_role_delete_cascades_to_role_permissions(db_session: AsyncSession):
    """Deleting a role removes its RolePermission rows."""
    role = make_system_role()
    perm = make_permission("post.create")
    db_session.add_all([role, perm])
    await db_session.flush()

    rp_id = (await db_session.flush(), make_role_permission(role.id, perm.id))[1]
    db_session.add(rp_id)
    await db_session.flush()
    rp_id_val = rp_id.id

    await db_session.delete(role)
    await db_session.flush()

    result = await db_session.execute(
        select(RolePermission).where(RolePermission.id == rp_id_val)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_permission_delete_cascades_to_role_permissions(db_session: AsyncSession):
    """Deleting a permission removes its RolePermission rows."""
    role = make_system_role()
    perm = make_permission("post.delete")
    db_session.add_all([role, perm])
    await db_session.flush()

    rp = make_role_permission(role.id, perm.id)
    db_session.add(rp)
    await db_session.flush()
    rp_id = rp.id

    await db_session.delete(perm)
    await db_session.flush()

    result = await db_session.execute(
        select(RolePermission).where(RolePermission.id == rp_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_role_permission_relationships(db_session: AsyncSession):
    role = make_system_role("Employee")
    perm = make_permission("post.create")
    db_session.add_all([role, perm])
    await db_session.flush()

    rp = make_role_permission(role.id, perm.id)
    db_session.add(rp)
    await db_session.flush()
    await db_session.refresh(rp, ["role", "permission"])

    assert rp.role.id == role.id
    assert rp.permission.id == perm.id


@pytest.mark.asyncio
async def test_role_permissions_relationship(db_session: AsyncSession):
    """role.role_permissions loads all granted permissions."""
    role = make_system_role("Super Admin")
    perm_a = make_permission("post.create")
    perm_b = make_permission("event.create", category="events")
    db_session.add_all([role, perm_a, perm_b])
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm_a.id))
    db_session.add(make_role_permission(role.id, perm_b.id))
    await db_session.flush()
    await db_session.refresh(role, ["role_permissions"])

    assert len(role.role_permissions) == 2


# ── UserRole — persistence ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_role_org_wide_persists(db_session: AsyncSession):
    """Org-wide assignment: branch_id=NULL."""
    user, org = await setup_org(db_session)
    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id)
    db_session.add(ur)
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.id == ur.id)
    )
    fetched = result.scalar_one()
    assert fetched.user_id == user.id
    assert fetched.role_id == role.id
    assert fetched.organization_id == org.id
    assert fetched.branch_id is None


@pytest.mark.asyncio
async def test_user_role_branch_scoped_persists(db_session: AsyncSession):
    """Branch-scoped assignment: branch_id set."""
    user, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)
    role = make_system_role("Branch Manager")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id, branch_id=branch.id)
    db_session.add(ur)
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.id == ur.id)
    )
    fetched = result.scalar_one()
    assert fetched.branch_id == branch.id
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_user_role_id_is_uuid_format(db_session: AsyncSession):
    user, org = await setup_org(db_session)
    role = make_system_role()
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id)
    db_session.add(ur)
    await db_session.flush()

    parts = ur.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_user_role_timestamps_auto_set(db_session: AsyncSession):
    user, org = await setup_org(db_session)
    role = make_system_role()
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id)
    db_session.add(ur)
    await db_session.flush()

    assert ur.created_at is not None
    assert ur.updated_at is not None


# ── UserRole — scoping rules ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_role_org_wide_and_branch_scoped_simultaneously(db_session: AsyncSession):
    """Core scoping rule: same role at org-wide AND branch scope co-exist.
    NULL branch_id and a real branch_id are treated as distinct by the
    unique constraint, so both rows are valid and independent."""
    user, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)
    role = make_system_role("Branch Manager")
    db_session.add(role)
    await db_session.flush()

    org_wide = make_user_role(user.id, role.id, org.id, branch_id=None)
    branch_scoped = make_user_role(user.id, role.id, org.id, branch_id=branch.id)
    db_session.add_all([org_wide, branch_scoped])
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.user_id == user.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_exact_duplicate_user_role_raises(db_session: AsyncSession):
    """The exact same (user, role, org, branch) cannot be inserted twice."""
    user, org = await setup_org(db_session)
    role = make_system_role()
    db_session.add(role)
    await db_session.flush()

    db_session.add(make_user_role(user.id, role.id, org.id))
    await db_session.flush()

    db_session.add(make_user_role(user.id, role.id, org.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_user_can_hold_multiple_different_roles(db_session: AsyncSession):
    """A user can hold multiple roles simultaneously."""
    user, org = await setup_org(db_session)

    role_a = make_system_role("Employee")
    role_b = make_system_role("Branch Manager")
    db_session.add_all([role_a, role_b])
    await db_session.flush()

    db_session.add(make_user_role(user.id, role_a.id, org.id))
    db_session.add(make_user_role(user.id, role_b.id, org.id))
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.user_id == user.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_same_role_can_be_held_by_multiple_users(db_session: AsyncSession):
    user_a, org = await setup_org(db_session, email="owner_a@example.com")
    user_b = make_user(organization_id=org.id, email="user_b@example.com")
    db_session.add(user_b)
    await db_session.flush()

    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    db_session.add(make_user_role(user_a.id, role.id, org.id))
    db_session.add(make_user_role(user_b.id, role.id, org.id))
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.role_id == role.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_branch_scoped_role_different_branches_same_user(db_session: AsyncSession):
    """User can hold the same role in multiple different branches."""
    user, org = await setup_org(db_session)
    branch_a = await setup_branch(db_session, org.id, name="Lagos Branch")
    branch_b = await setup_branch(db_session, org.id, name="Abuja Branch")
    role = make_system_role("Branch Manager")
    db_session.add(role)
    await db_session.flush()

    db_session.add(make_user_role(user.id, role.id, org.id, branch_id=branch_a.id))
    db_session.add(make_user_role(user.id, role.id, org.id, branch_id=branch_b.id))
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.user_id == user.id)
    )
    assert len(result.scalars().all()) == 2


# ── UserRole — audit trail ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_role_assigned_by_persists(db_session: AsyncSession):
    """assigned_by records who granted the role."""
    owner, org = await setup_org(db_session, email="owner@example.com")
    member = make_user(organization_id=org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(member.id, role.id, org.id, assigned_by=owner.id)
    db_session.add(ur)
    await db_session.flush()

    assert ur.assigned_by == owner.id


@pytest.mark.asyncio
async def test_assigned_by_set_null_on_assigner_delete(db_session: AsyncSession):
    """If the assigner is deleted, assigned_by is nulled — the UserRole
    row survives as an audit record without a live assigner link."""
    owner, org = await setup_org(db_session, email="owner@example.com")
    member = make_user(organization_id=org.id, email="member@example.com")
    assigner = make_user(organization_id=org.id, email="assigner@example.com")
    db_session.add_all([member, assigner])
    await db_session.flush()

    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(member.id, role.id, org.id, assigned_by=assigner.id)
    db_session.add(ur)
    await db_session.flush()
    ur_id = ur.id

    await db_session.delete(assigner)
    await db_session.flush()

    result = await db_session.execute(select(UserRole).where(UserRole.id == ur_id))
    fetched = result.scalar_one()
    assert fetched.assigned_by is None
    assert fetched.user_id == member.id


@pytest.mark.asyncio
async def test_assigned_by_nullable_for_system_seeded_assignments(db_session: AsyncSession):
    """System-seeded role grants (e.g. owner's Super Admin at signup)
    have no assigning user — assigned_by is nullable."""
    user, org = await setup_org(db_session)
    role = make_system_role("Super Admin")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id, assigned_by=None)
    db_session.add(ur)
    await db_session.flush()

    assert ur.assigned_by is None


# ── UserRole — cascade behaviour ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_delete_cascades_to_user_roles(db_session: AsyncSession):
    """Deleting a user removes all their role assignments."""
    owner, org = await setup_org(db_session, email="owner@example.com")
    member = make_user(organization_id=org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()
    member_id = member.id

    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    db_session.add(make_user_role(member.id, role.id, org.id))
    await db_session.flush()

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.user_id == member_id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_org_delete_cascades_to_user_roles(db_session: AsyncSession):
    """Deleting an org removes all role assignments scoped to it."""
    user, org = await setup_org(db_session)
    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    db_session.add(make_user_role(user.id, role.id, org.id))
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.organization_id == org.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_role_delete_cascades_to_user_roles(db_session: AsyncSession):
    """Deleting a role removes all its user assignments."""
    user, org = await setup_org(db_session)
    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    db_session.add(make_user_role(user.id, role.id, org.id))
    await db_session.flush()

    await db_session.delete(role)
    await db_session.flush()

    result = await db_session.execute(
        select(UserRole).where(UserRole.role_id == role.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_branch_delete_cascades_to_branch_scoped_user_roles(db_session: AsyncSession):
    """Deleting a branch removes branch-scoped role assignments for it.
    Org-wide assignments for the same user survive."""
    user, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)
    role = make_system_role("Branch Manager")
    db_session.add(role)
    await db_session.flush()

    org_wide = make_user_role(user.id, role.id, org.id, branch_id=None)
    branch_scoped = make_user_role(user.id, role.id, org.id, branch_id=branch.id)
    db_session.add_all([org_wide, branch_scoped])
    await db_session.flush()
    org_wide_id = org_wide.id

    await db_session.delete(branch)
    await db_session.flush()

    # Branch-scoped assignment gone
    result = await db_session.execute(
        select(UserRole).where(UserRole.branch_id == branch.id)
    )
    assert result.scalars().all() == []

    # Org-wide assignment survives
    result = await db_session.execute(
        select(UserRole).where(UserRole.id == org_wide_id)
    )
    assert result.scalar_one_or_none() is not None


# ── UserRole — relationships ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_role_relationships(db_session: AsyncSession):
    user, org = await setup_org(db_session)
    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id)
    db_session.add(ur)
    await db_session.flush()
    await db_session.refresh(ur, ["user", "role", "organization"])

    assert ur.user.id == user.id
    assert ur.role.id == role.id
    assert ur.organization.id == org.id
    assert ur.branch is None


@pytest.mark.asyncio
async def test_user_role_branch_relationship(db_session: AsyncSession):
    user, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)
    role = make_system_role("Branch Manager")
    db_session.add(role)
    await db_session.flush()

    ur = make_user_role(user.id, role.id, org.id, branch_id=branch.id)
    db_session.add(ur)
    await db_session.flush()
    await db_session.refresh(ur, ["branch"])

    assert ur.branch is not None
    assert ur.branch.id == branch.id


@pytest.mark.asyncio
async def test_user_user_roles_relationship(db_session: AsyncSession):
    """user.user_roles loads all assignments for that user."""
    user, org = await setup_org(db_session)
    role_a = make_system_role("Employee")
    role_b = make_system_role("Branch Manager")
    db_session.add_all([role_a, role_b])
    await db_session.flush()

    db_session.add(make_user_role(user.id, role_a.id, org.id))
    db_session.add(make_user_role(user.id, role_b.id, org.id))
    await db_session.flush()
    await db_session.refresh(user, ["user_roles"])

    assert len(user.user_roles) == 2


@pytest.mark.asyncio
async def test_org_user_roles_relationship(db_session: AsyncSession):
    """org.user_roles loads all role assignments scoped to this org."""
    user, org = await setup_org(db_session)
    role = make_system_role("Employee")
    db_session.add(role)
    await db_session.flush()

    member = make_user(organization_id=org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_user_role(user.id, role.id, org.id))
    db_session.add(make_user_role(member.id, role.id, org.id))
    await db_session.flush()
    await db_session.refresh(org, ["user_roles"])

    assert len(org.user_roles) == 2


# ── Permission check query ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_permission_check_query_org_wide(db_session: AsyncSession):
    """Verifies the permission-check join pattern documented in the model:
    a user with a role that has a given permission, assigned org-wide,
    passes the check for any branch in that org."""
    user, org = await setup_org(db_session)

    role = make_system_role("Employee")
    perm = make_permission("post.create")
    db_session.add_all([role, perm])
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm.id))
    db_session.add(make_user_role(user.id, role.id, org.id, branch_id=None))
    await db_session.flush()

    # The permission check query from the model docstring
    result = await db_session.execute(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(
            UserRole.user_id == user.id,
            UserRole.organization_id == org.id,
            Permission.name == "post.create",
        )
        .limit(1)
    )
    assert result.scalar_one_or_none() == "post.create"


@pytest.mark.asyncio
async def test_permission_check_query_branch_scoped(db_session: AsyncSession):
    """A branch-scoped role assignment grants the permission only within
    that branch — but the OR (branch_id = X OR branch_id IS NULL) clause
    means the org-wide assignment also satisfies the check."""
    user, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)

    role = make_system_role("Branch Manager")
    perm = make_permission("branch.manage")
    db_session.add_all([role, perm])
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm.id))
    db_session.add(make_user_role(user.id, role.id, org.id, branch_id=branch.id))
    await db_session.flush()

    # Check with branch scope — should pass
    result = await db_session.execute(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(
            UserRole.user_id == user.id,
            UserRole.organization_id == org.id,
            (UserRole.branch_id == branch.id) | (UserRole.branch_id == None),
            Permission.name == "branch.manage",
        )
        .limit(1)
    )
    assert result.scalar_one_or_none() == "branch.manage"


@pytest.mark.asyncio
async def test_permission_check_returns_nothing_for_ungranted_permission(db_session: AsyncSession):
    """A user with a role that does NOT have a given permission fails the
    check — the join returns no rows."""
    user, org = await setup_org(db_session)

    role = make_system_role("Employee")
    granted_perm = make_permission("post.create")
    unganted_perm = make_permission("post.delete")
    db_session.add_all([role, granted_perm, unganted_perm])
    await db_session.flush()

    # Only grant post.create, NOT post.delete
    db_session.add(make_role_permission(role.id, granted_perm.id))
    db_session.add(make_user_role(user.id, role.id, org.id))
    await db_session.flush()

    result = await db_session.execute(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(
            UserRole.user_id == user.id,
            UserRole.organization_id == org.id,
            Permission.name == "post.delete",
        )
        .limit(1)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cross_tenant_permission_isolation(db_session: AsyncSession):
    """A role assignment in org A does not grant permissions in org B —
    the organization_id filter in the permission check enforces this."""
    user_a, org_a = await setup_org(
        db_session, email="owner_a@example.com", slug="org-a"
    )
    _, org_b = await setup_org(
        db_session, email="owner_b@example.com", slug="org-b"
    )

    role = make_system_role("Employee")
    perm = make_permission("post.create")
    db_session.add_all([role, perm])
    await db_session.flush()

    db_session.add(make_role_permission(role.id, perm.id))
    # Assign role to user_a in org_a only
    db_session.add(make_user_role(user_a.id, role.id, org_a.id))
    await db_session.flush()

    # Check against org_b — must return nothing
    result = await db_session.execute(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(
            UserRole.user_id == user_a.id,
            UserRole.organization_id == org_b.id,
            Permission.name == "post.create",
        )
        .limit(1)
    )
    assert result.scalar_one_or_none() is None