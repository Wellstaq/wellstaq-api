"""
Branch model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch
from app.models import Organization
from app.models import User


# ── Factories ──────────────────────────────────────────────────────────────────

def make_user(email: str = "user@example.com", **overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        role_name="overall_manager",
        hashed_password="$2b$12$fakehash",
    )
    return User(**{**defaults, **overrides})


def make_org(owner_id: str, **overrides) -> Organization:
    defaults = dict(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id=owner_id,
    )
    return Organization(**{**defaults, **overrides})


def make_branch(organization_id: str, **overrides) -> Branch:
    defaults = dict(
        name="Lagos Branch",
        organization_id=organization_id,
    )
    return Branch(**{**defaults, **overrides})


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(db_session: AsyncSession, email: str = "owner@example.com", slug: str = "acme-corp"):
    """Create and flush a user + org, return both."""
    user = make_user(email=email)
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=slug)
    db_session.add(org)
    await db_session.flush()

    return user, org


# ── Branch model ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_branch_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    result = await db_session.execute(select(Branch).where(Branch.id == branch.id))
    fetched = result.scalar_one()
    assert fetched.name == "Lagos Branch"
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_branch_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    parts = branch.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_branch_timestamps_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    assert branch.created_at is not None
    assert branch.updated_at is not None


@pytest.mark.asyncio
async def test_branch_optional_fields_nullable(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    assert branch.manager_id is None
    assert branch.created_by is None


@pytest.mark.asyncio
async def test_branch_with_manager(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
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
    _, org = await setup_org(db_session)

    branch = make_branch(org.id, manager_id="00000000-0000-0000-0000-000000000000")
    db_session.add(branch)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_branch_cascades_on_org_delete(db_session: AsyncSession):
    """Deleting an org should cascade delete its branches."""
    _, org = await setup_org(db_session)

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
async def test_branch_manager_cannot_be_deleted(db_session: AsyncSession):
    """ondelete=RESTRICT — cannot delete a user who manages a branch."""
    _, org = await setup_org(db_session)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    branch = make_branch(org.id, manager_id=manager.id)
    db_session.add(branch)
    await db_session.flush()

    # Use raw SQL to bypass ORM relationship handling and hit the DB FK directly
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM users WHERE id = :id"),
            {"id": manager.id}
        )
        await db_session.flush()

@pytest.mark.asyncio
async def test_manager_can_be_deleted_after_branch_is_deleted(db_session: AsyncSession):
    """Once the branch is gone, the manager user can be deleted freely."""
    _, org = await setup_org(db_session)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    branch = make_branch(org.id, manager_id=manager.id)
    db_session.add(branch)
    await db_session.flush()

    # Delete the branch first
    await db_session.delete(branch)
    await db_session.flush()

    # Now the manager can be deleted without constraint violation
    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"),
        {"id": manager.id}
    )
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == manager.id))
    assert result.scalar_one_or_none() is None
    
@pytest.mark.asyncio
async def test_org_can_have_multiple_branches(db_session: AsyncSession):
    _, org = await setup_org(db_session)

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
    owner, org = await setup_org(db_session)

    branch = make_branch(org.id, created_by=owner.id)
    db_session.add(branch)
    await db_session.flush()

    # Reassign org owner before deleting to avoid RESTRICT on owner_id
    new_owner = make_user(email="new_owner@example.com")
    db_session.add(new_owner)
    await db_session.flush()
    org.owner_id = new_owner.id
    await db_session.flush()

    await db_session.delete(owner)
    await db_session.flush()

    result = await db_session.execute(select(Branch).where(Branch.id == branch.id))
    fetched = result.scalar_one()
    # created_by still holds the old user's id — audit trail preserved
    assert fetched.created_by == owner.id


# ── Members ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_can_be_assigned_to_branch(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = make_user(email="member@example.com", role_name="employee", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == member.id))
    fetched = result.scalar_one()
    assert fetched.branch_id == branch.id


@pytest.mark.asyncio
async def test_branch_can_have_multiple_members(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    db_session.add(make_user(email="m1@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(make_user(email="m2@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(make_user(email="m3@example.com", role_name="employee", branch_id=branch.id))
    await db_session.flush()

    result = await db_session.execute(
        select(User).where(User.branch_id == branch.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_branch_delete_nullifies_member_branch_id(db_session: AsyncSession):
    """ondelete=SET NULL — deleting a branch detaches members, doesn't delete them."""
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = make_user(email="member@example.com", role_name="employee", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    await db_session.delete(branch)
    await db_session.flush()
    await db_session.refresh(member)

    assert member.branch_id is None  # user still exists, just detached
    assert member.id is not None     # user row not deleted


@pytest.mark.asyncio
async def test_user_branch_id_fk_must_exist(db_session: AsyncSession):
    """branch_id on user must reference a real branch."""
    _, org = await setup_org(db_session)

    member = make_user(
        email="member@example.com",
        role_name="employee",
        branch_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(member)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_can_have_no_branch(db_session: AsyncSession):
    """branch_id is nullable — user can exist without a branch."""
    _, org = await setup_org(db_session)

    user = make_user(email="nobranch@example.com", role_name="employee")
    db_session.add(user)
    await db_session.flush()

    assert user.branch_id is None


# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_branch_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()
    await db_session.refresh(branch, ["organization"])

    assert branch.organization is not None
    assert branch.organization.id == org.id


@pytest.mark.asyncio
async def test_branch_manager_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
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
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    db_session.add(make_user(email="m1@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(make_user(email="m2@example.com", role_name="employee", branch_id=branch.id))
    await db_session.flush()
    await db_session.refresh(branch, ["members"])

    assert len(branch.members) == 2
    assert all(m.branch_id == branch.id for m in branch.members)


@pytest.mark.asyncio
async def test_user_branch_relationship(db_session: AsyncSession):
    """User.branch back-ref resolves to the correct branch."""
    _, org = await setup_org(db_session)

    branch = make_branch(org.id)
    db_session.add(branch)
    await db_session.flush()

    member = make_user(email="member@example.com", role_name="employee", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()
    await db_session.refresh(member, ["branch"])

    assert member.branch is not None
    assert member.branch.id == branch.id


@pytest.mark.asyncio
async def test_organization_branches_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    db_session.add(make_branch(org.id, name="Branch A"))
    db_session.add(make_branch(org.id, name="Branch B"))
    await db_session.flush()
    await db_session.refresh(org, ["branches"])

    assert len(org.branches) == 2


@pytest.mark.asyncio
async def test_user_managed_branches_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    db_session.add(make_branch(org.id, name="Branch A", manager_id=manager.id))
    db_session.add(make_branch(org.id, name="Branch B", manager_id=manager.id))
    await db_session.flush()
    await db_session.refresh(manager, ["managed_branches"])

    assert len(manager.managed_branches) == 2