"""
Branch model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Schema change this file reflects:
- User.role_name (a free string) has been replaced by User.role_id, a
  FK to roles.id (see Permission/Role model). Every place this file
  used to call make_user(org.id, ..., role_name="...") now calls
  await make_user_with_role(org.id, ..., role_name="...") instead —
  make_user() itself no longer accepts role_name and has no role
  default of its own; role resolution requires a DB round-trip (to
  find-or-create the system Role), which is why make_user_with_role()
  is async where make_user() is not.
- Added coverage for Branch.role, the branch-scoped Role relationship
  (see Role model: branch-scoped custom roles, is_system=False,
  organization_id SET, branch_id SET). This relationship existed in
  the model but had no test coverage before. It also exercises the
  passive_deletes=True fix on Branch.role: cascade="all,
  delete-orphan" on the ORM side together with ondelete="CASCADE" on
  Role.branch_id at the DB level were racing over the same row
  without passive_deletes=True — see the model's updated comment.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Organization, Role, User
from tests.conftest import make_org_with_owner, make_user, make_user_with_role

# ── Local Factories ────────────────────────────────────────────────────────────

def make_branch(organization_id: str, **overrides) -> Branch:
    defaults = dict(
        name="Lagos Branch",
        organization_id=organization_id,
    )
    return Branch(**{**defaults, **overrides})


def make_branch_role(organization_id: str, branch_id: str, **overrides) -> Role:
    """A branch-scoped custom Role (is_system=False, organization_id SET,
    branch_id SET) — see Role model docstring, scope tier 3."""
    defaults = dict(
        name="branch_coordinator",
        organization_id=organization_id,
        branch_id=branch_id,
        is_system=False,
    )
    return Role(**{**defaults, **overrides})


# ── Branch Model Tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_branch_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    result = await db_session.execute(select(Branch).where(Branch.id == branch.id))
    fetched = result.scalar_one()
    assert fetched.name == "Lagos Branch"
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_branch_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    parts = branch.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_branch_timestamps_auto_set(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    assert branch.created_at is not None
    assert branch.updated_at is not None


@pytest.mark.asyncio
async def test_branch_optional_fields_nullable(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    assert branch.manager_id is None
    assert branch.created_by is None


@pytest.mark.asyncio
async def test_branch_with_manager(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id, email="manager@example.com", role_name="branch_manager"
    )
    db_session.add(manager)
    await db_session.flush()

    branch = make_branch(org.id, manager_id=manager.id, created_by=owner.id)
    db_session.add(branch)
    await db_session.flush()

    result = await db_session.execute(select(Branch).where(Branch.id == branch.id))
    fetched = result.scalar_one()
    assert fetched.manager_id == manager.id
    assert fetched.created_by == owner.id


@pytest.mark.asyncio
async def test_branch_org_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    branch = make_branch(organization_id="00000000-0000-0000-0000-000000000000")
    db_session.add(branch)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_branch_manager_fk_must_exist(db_session: AsyncSession):
    """manager_id must reference a real user if provided."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id, manager_id="00000000-0000-0000-0000-000000000000")
    db_session.add(branch)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_branch_cascades_on_org_delete(db_session: AsyncSession):
    """Deleting an org should cascade delete its branches."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_branch(org.id, name="Branch A"))
    db_session.add(make_branch(org.id, name="Branch B"))
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(Branch).where(Branch.organization_id == org.id)
    )
    assert result.scalars().all() == []



@pytest.mark.asyncio
async def test_manager_can_be_deleted_after_branch_is_deleted(db_session: AsyncSession):
    """Once the branch is gone, the manager user can be deleted freely."""
    org, _ = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id, email="manager@example.com", role_name="branch_manager"
    )
    db_session.add(manager)
    await db_session.flush()

    branch = make_branch(org.id, manager_id=manager.id)
    db_session.add(branch)
    await db_session.flush()

    await db_session.delete(branch)
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"),
        {"id": manager.id}
    )
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == manager.id))
    assert result.scalar_one_or_none() is None

    
@pytest.mark.asyncio
async def test_org_can_have_multiple_branches(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_branch(org.id, name="Lagos Branch"))
    db_session.add(make_branch(org.id, name="Abuja Branch"))
    db_session.add(make_branch(org.id, name="Port Harcourt Branch"))
    await db_session.flush()

    result = await db_session.execute(
        select(Branch).where(Branch.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_created_by_is_plain_string_no_fk(db_session: AsyncSession):
    """created_by is an audit string — no FK, so deleted user ID is retained."""
    org, owner = await make_org_with_owner(db_session)

    branch = make_branch(org.id, created_by=owner.id)
    db_session.add(branch)
    await db_session.flush()

    new_owner = await make_user_with_role(db_session, org.id, email="new_owner@example.com")
    db_session.add(new_owner)
    await db_session.flush()
    org.owner_id = new_owner.id
    await db_session.flush()

    await db_session.delete(owner)
    await db_session.flush()

    result = await db_session.execute(select(Branch).where(Branch.id == branch.id))
    fetched = result.scalar_one()
    assert fetched.created_by == owner.id


# ── Database Guardrails / Constraints Verification ───────────────────────────

@pytest.mark.asyncio
async def test_branch_name_uniqueness_per_organization(db_session: AsyncSession):
    """Enforce unique names inside an organization, but allow identical names across separate orgs."""
    org1, _ = await make_org_with_owner(db_session, org_kwargs={"slug": "org-one"}, user_kwargs={"email": "owner1@example.com"})
    org2, _ = await make_org_with_owner(db_session, org_kwargs={"slug": "org-two"}, user_kwargs={"email": "owner2@example.com"})

    # Allowed: Same branch name inside entirely separate corporate spaces
    db_session.add(make_branch(org1.id, name="Lagos Branch"))
    db_session.add(make_branch(org2.id, name="Lagos Branch"))
    await db_session.flush()

    # Rejected: Duplicate branch name inside the exact same organization context
    db_session.add(make_branch(org1.id, name="Lagos Branch"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_branch_name_cannot_be_empty_or_whitespace(db_session: AsyncSession):
    """CheckConstraint must reject empty strings or whitespace values."""
    org, _ = await make_org_with_owner(db_session)

    # Empty String
    db_session.add(make_branch(org.id, name=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()
        
    await db_session.rollback()

    # Whitespace characters only
    db_session.add(make_branch(org.id, name="      "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Members ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_can_be_assigned_to_branch(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = await make_user_with_role(
        db_session, org.id, email="member@example.com", role_name="employee", branch_id=branch.id
    )
    db_session.add(member)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == member.id))
    fetched = result.scalar_one()
    assert fetched.branch_id == branch.id


@pytest.mark.asyncio
async def test_branch_can_have_multiple_members(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    db_session.add(await make_user_with_role(db_session, org.id, email="m1@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(await make_user_with_role(db_session, org.id, email="m2@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(await make_user_with_role(db_session, org.id, email="m3@example.com", role_name="employee", branch_id=branch.id))
    await db_session.flush()

    result = await db_session.execute(
        select(User).where(User.branch_id == branch.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_branch_delete_nullifies_member_branch_id(db_session: AsyncSession):
    """ondelete=SET NULL — deleting a branch detaches members, doesn't drop them."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = await make_user_with_role(
        db_session, org.id, email="member@example.com", role_name="employee", branch_id=branch.id
    )
    db_session.add(member)
    await db_session.flush()

    await db_session.delete(branch)
    await db_session.flush()
    await db_session.refresh(member)

    assert member.branch_id is None
    assert member.id is not None


@pytest.mark.asyncio
async def test_user_branch_id_fk_must_exist(db_session: AsyncSession):
    """branch_id on user must reference a real branch."""
    org, _ = await make_org_with_owner(db_session)

    member = await make_user_with_role(
        db_session,
        org.id,
        email="member@example.com",
        role_name="employee",
        branch_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(member)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_can_have_no_branch(db_session: AsyncSession):
    """branch_id is nullable — user can exist without a branch assignment."""
    org, _ = await make_org_with_owner(db_session)

    user = await make_user_with_role(db_session, org.id, email="nobranch@example.com", role_name="employee")
    db_session.add(user)
    await db_session.flush()

    assert user.branch_id is None


# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_branch_organization_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()
    await db_session.refresh(branch, ["organization"])

    assert branch.organization is not None
    assert branch.organization.id == org.id


@pytest.mark.asyncio
async def test_branch_manager_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id, email="manager@example.com", role_name="branch_manager"
    )
    db_session.add(manager)
    await db_session.flush()

    branch = make_branch(org.id, manager_id=manager.id)
    db_session.add(branch)
    await db_session.flush()
    await db_session.refresh(branch, ["manager"])

    assert branch.manager is not None
    assert branch.manager.id == manager.id


@pytest.mark.asyncio
async def test_branch_members_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    db_session.add(await make_user_with_role(db_session, org.id, email="m1@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(await make_user_with_role(db_session, org.id, email="m2@example.com", role_name="employee", branch_id=branch.id))
    await db_session.flush()
    await db_session.refresh(branch, ["members"])

    assert len(branch.members) == 2
    assert all(m.branch_id == branch.id for m in branch.members)


@pytest.mark.asyncio
async def test_user_branch_relationship(db_session: AsyncSession):
    """User.branch back-ref resolves to the correct branch instance."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = await make_user_with_role(
        db_session, org.id, email="member@example.com", role_name="employee", branch_id=branch.id
    )
    db_session.add(member)
    await db_session.flush()
    await db_session.refresh(member, ["branch"])

    assert member.branch is not None
    assert member.branch.id == branch.id


@pytest.mark.asyncio
async def test_organization_branches_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_branch(org.id, name="Branch A"))
    db_session.add(make_branch(org.id, name="Branch B"))
    await db_session.flush()
    await db_session.refresh(org, ["branches"])

    assert len(org.branches) == 2


@pytest.mark.asyncio
async def test_user_managed_branches_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id, email="manager@example.com", role_name="branch_manager"
    )
    db_session.add(manager)
    await db_session.flush()

    db_session.add(make_branch(org.id, name="Branch A", manager_id=manager.id))
    db_session.add(make_branch(org.id, name="Branch B", manager_id=manager.id))
    await db_session.flush()
    await db_session.refresh(manager, ["managed_branches"])

    assert len(manager.managed_branches) == 2


# ── Branch-scoped Role (RBAC) ───────────────────────────────────────────────────
#
# Branch.role is a one-to-one relationship to a branch-scoped Role row
# (Role scope tier 3: is_system=False, organization_id SET, branch_id
# SET — see Role model docstring). This had no coverage prior to this
# update. It also exercises passive_deletes=True on Branch.role, added
# to defer branch-deletion cascade entirely to the DB's
# ondelete="CASCADE" on Role.branch_id instead of racing the ORM's own
# cascade="all, delete-orphan" over the same row.

@pytest.mark.asyncio
async def test_branch_can_have_scoped_role(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role = make_branch_role(org.id, branch.id)
    db_session.add(role)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id == role.id))
    fetched = result.scalar_one()
    assert fetched.branch_id == branch.id
    assert fetched.organization_id == org.id
    assert fetched.is_system is False


@pytest.mark.asyncio
async def test_branch_role_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role = make_branch_role(org.id, branch.id)
    db_session.add(role)
    await db_session.flush()
    await db_session.refresh(branch, ["role"])

    assert branch.role is not None
    assert branch.role.id == role.id


@pytest.mark.asyncio
async def test_branch_without_role_is_none(db_session: AsyncSession):
    """A branch can exist without ever having a scoped Role assigned."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()
    await db_session.refresh(branch, ["role"])

    assert branch.role is None


@pytest.mark.asyncio
async def test_branch_deletion_cascades_to_scoped_role(db_session: AsyncSession):
    """Deleting a branch deletes its branch-scoped Role too — via the
    DB's ondelete="CASCADE" on Role.branch_id, deferred to by
    passive_deletes=True on Branch.role. Without that flag, the ORM's
    own cascade="all, delete-orphan" would try to compute/issue its own
    DELETE for the same Role row at the same time, which is the
    collision this test guards against regressing to."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role = make_branch_role(org.id, branch.id)
    db_session.add(role)
    await db_session.flush()
    role_id = role.id

    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id == role_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_org_deletion_cascades_to_branch_scoped_roles(db_session: AsyncSession):
    """Deleting the org cascades through its branches (Organization ->
    Branch via organization_id ondelete=CASCADE), which in turn must
    still take their scoped Roles with them, two FK hops down."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role = make_branch_role(org.id, branch.id)
    db_session.add(role)
    await db_session.flush()
    role_id = role.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id == role_id))
    assert result.scalar_one_or_none() is None