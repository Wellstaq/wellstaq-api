"""
Branch model tests.

REVISED for the branch/role redesign:
- User.branch_id is now NOT NULL — every user (including the org
  owner, via make_org_with_owner's new Headquarters branch) has a home
  branch from the moment they're created. Tests that used to create a
  user with no branch, or that asserted branch deletion nulls out
  member branch_id, no longer apply and are replaced below.
- Branch.role (one-to-one) is now Branch.roles (one-to-many) — a
  branch can have many custom Role rows, not at most one. Tests
  renamed and a new one added covering multiple roles per branch.
- Deleting a branch now CASCADEs to any user whose home branch_id
  points at it (previously SET NULL, which is impossible on a NOT
  NULL column) — see User model / Branch model docstrings. This is a
  DB-level safety net only; in real application flow,
  delete_branch.py's service-layer guard should already have
  transferred every home-branch member out before the DB delete ever
  runs, so this cascade firing against real data would mean that
  guard was bypassed. The model-level test below confirms the DB
  behavior directly, since these tests don't go through the service.
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
    branch_id SET — the only custom-role tier that exists now)."""
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
        db_session, org.id,
        email="manager@example.com",
        role_name="branch_manager",
        branch_id=owner.branch_id,  # home branch required now — HQ is fine
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
    """Once the branch is gone, the manager user can be deleted freely
    — as long as their OWN home branch isn't the one just deleted.
    Here the manager's home is the org's HQ (via owner.branch_id),
    separate from the branch they managed, so deleting that branch
    doesn't touch them at all."""
    org, owner = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id,
        email="manager@example.com",
        role_name="branch_manager",
        branch_id=owner.branch_id,
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
    # +1 for the Headquarters branch auto-created by make_org_with_owner
    assert len(result.scalars().all()) == 4


@pytest.mark.asyncio
async def test_created_by_is_plain_string_no_fk(db_session: AsyncSession):
    """created_by is an audit string — no FK, so deleted user ID is retained."""
    org, owner = await make_org_with_owner(db_session)

    branch = make_branch(org.id, created_by=owner.id)
    db_session.add(branch)
    await db_session.flush()

    new_owner = await make_user_with_role(
        db_session, org.id,
        email="new_owner@example.com",
        branch_id=owner.branch_id,
    )
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

    db_session.add(make_branch(org1.id, name="Lagos Branch"))
    db_session.add(make_branch(org2.id, name="Lagos Branch"))
    await db_session.flush()

    db_session.add(make_branch(org1.id, name="Lagos Branch"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_branch_name_cannot_be_empty_or_whitespace(db_session: AsyncSession):
    """CheckConstraint must reject empty strings or whitespace values."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_branch(org.id, name=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_branch(org.id, name="      "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Members (home branch_id) ─────────────────────────────────────────────────

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
async def test_branch_delete_cascades_to_members(db_session: AsyncSession):
    """
    REVISED (was test_branch_delete_nullifies_member_branch_id):
    branch_id is NOT NULL now, so ondelete=SET NULL is impossible.
    Deleting a branch that still has home-branch members now CASCADEs
    and deletes those User rows along with it — see User.branch_id's
    ondelete="CASCADE" and Branch.members' passive_deletes=True.

    This is a DB-level fact, not the recommended application path —
    delete_branch.py's service layer should always transfer members
    out BEFORE calling delete, specifically to avoid ever triggering
    this cascade against real users. This test documents what the raw
    DB does when that guard is bypassed (as it is here, since this
    test operates directly on the model layer).
    """
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = await make_user_with_role(
        db_session, org.id, email="member@example.com", role_name="employee", branch_id=branch.id
    )
    db_session.add(member)
    await db_session.flush()
    member_id = member.id

    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == member_id))
    assert result.scalar_one_or_none() is None


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
async def test_user_branch_id_is_required(db_session: AsyncSession):
    """
    REVISED (was test_user_can_have_no_branch): branch_id is NOT NULL
    now — every user must have a home branch from creation. Omitting
    it (or passing None) must raise, not silently succeed.
    """
    org, _ = await make_org_with_owner(db_session)

    user = User(
        first_name="No",
        last_name="Branch",
        email="nobranch@example.com",
        hashed_password="$2b$12$fakehash",
        organization_id=org.id,
        branch_id=None,
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


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
    org, owner = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id,
        email="manager@example.com",
        role_name="branch_manager",
        branch_id=owner.branch_id,
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

    # +1 for the Headquarters branch auto-created by make_org_with_owner
    assert len(org.branches) == 3


@pytest.mark.asyncio
async def test_user_managed_branches_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    manager = await make_user_with_role(
        db_session, org.id,
        email="manager@example.com",
        role_name="branch_manager",
        branch_id=owner.branch_id,
    )
    db_session.add(manager)
    await db_session.flush()

    db_session.add(make_branch(org.id, name="Branch A", manager_id=manager.id))
    db_session.add(make_branch(org.id, name="Branch B", manager_id=manager.id))
    await db_session.flush()
    await db_session.refresh(manager, ["managed_branches"])

    assert len(manager.managed_branches) == 2


# ── Branch-scoped Roles (RBAC) ────────────────────────────────────────────────
#
# REVISED: Branch.role (one-to-one) is now Branch.roles (one-to-many)
# — a branch can have MANY custom Role rows, not at most one. Tests
# renamed accordingly, plus a new test confirming multiple roles are
# actually allowed on the same branch (impossible to express under the
# old one-to-one shape).

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
async def test_branch_roles_relationship(db_session: AsyncSession):
    """RENAMED (was test_branch_role_relationship): branch.roles is now
    a list."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role = make_branch_role(org.id, branch.id)
    db_session.add(role)
    await db_session.flush()
    await db_session.refresh(branch, ["roles"])

    assert len(branch.roles) == 1
    assert branch.roles[0].id == role.id


@pytest.mark.asyncio
async def test_branch_can_have_multiple_custom_roles(db_session: AsyncSession):
    """
    NEW: confirms the one-to-many shape actually works — a branch can
    have several independent custom roles at once, e.g.
    "branch_coordinator" and "floor_supervisor" both scoped to the
    same branch. This was structurally impossible under the old
    one-to-one Branch.role relationship.
    """
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role_a = make_branch_role(org.id, branch.id, name="branch_coordinator")
    role_b = make_branch_role(org.id, branch.id, name="floor_supervisor")
    db_session.add(role_a)
    db_session.add(role_b)
    await db_session.flush()
    await db_session.refresh(branch, ["roles"])

    assert len(branch.roles) == 2
    assert {r.name for r in branch.roles} == {"branch_coordinator", "floor_supervisor"}


@pytest.mark.asyncio
async def test_branch_without_roles_is_empty_list(db_session: AsyncSession):
    """RENAMED (was test_branch_without_role_is_none): a branch with no
    custom roles has an empty list, not None, since roles is now a
    collection."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()
    await db_session.refresh(branch, ["roles"])

    assert branch.roles == []


@pytest.mark.asyncio
async def test_branch_deletion_cascades_to_scoped_roles(db_session: AsyncSession):
    """RENAMED (was test_branch_deletion_cascades_to_scoped_role):
    deleting a branch deletes ALL its custom roles, not just one —
    via the DB's ondelete="CASCADE" on Role.branch_id."""
    org, _ = await make_org_with_owner(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    role_a = make_branch_role(org.id, branch.id, name="branch_coordinator")
    role_b = make_branch_role(org.id, branch.id, name="floor_supervisor")
    db_session.add(role_a)
    db_session.add(role_b)
    await db_session.flush()
    role_ids = [role_a.id, role_b.id]

    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(select(Role).where(Role.id.in_(role_ids)))
    assert result.scalars().all() == []


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