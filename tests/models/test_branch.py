"""
Branch model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Organization, User
from tests.conftest import make_user, make_org_with_owner

# ── Local Factories ────────────────────────────────────────────────────────────

def make_branch(organization_id: str, **overrides) -> Branch:
    defaults = dict(
        name="Lagos Branch",
        organization_id=organization_id,
    )
    return Branch(**{**defaults, **overrides})


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

    manager = make_user(org.id, email="manager@example.com", role_name="branch_manager")
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
async def test_branch_manager_cannot_be_deleted(db_session: AsyncSession):
    """ondelete=RESTRICT — cannot delete a user who manages a branch."""
    org, _ = await make_org_with_owner(db_session)

    manager = make_user(org.id, email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    branch = make_branch(org.id, manager_id=manager.id)
    db_session.add(branch)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM users WHERE id = :id"),
            {"id": manager.id}
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_manager_can_be_deleted_after_branch_is_deleted(db_session: AsyncSession):
    """Once the branch is gone, the manager user can be deleted freely."""
    org, _ = await make_org_with_owner(db_session)

    manager = make_user(org.id, email="manager@example.com", role_name="branch_manager")
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

    new_owner = make_user(org.id, email="new_owner@example.com")
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

    member = make_user(org.id, email="member@example.com", role_name="employee", branch_id=branch.id)
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

    db_session.add(make_user(org.id, email="m1@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(make_user(org.id, email="m2@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(make_user(org.id, email="m3@example.com", role_name="employee", branch_id=branch.id))
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

    member = make_user(org.id, email="member@example.com", role_name="employee", branch_id=branch.id)
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

    member = make_user(
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

    user = make_user(org.id, email="nobranch@example.com", role_name="employee")
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

    manager = make_user(org.id, email="manager@example.com", role_name="branch_manager")
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

    db_session.add(make_user(org.id, email="m1@example.com", role_name="employee", branch_id=branch.id))
    db_session.add(make_user(org.id, email="m2@example.com", role_name="employee", branch_id=branch.id))
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

    member = make_user(org.id, email="member@example.com", role_name="employee", branch_id=branch.id)
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

    manager = make_user(org.id, email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    db_session.add(make_branch(org.id, name="Branch A", manager_id=manager.id))
    db_session.add(make_branch(org.id, name="Branch B", manager_id=manager.id))
    await db_session.flush()
    await db_session.refresh(manager, ["managed_branches"])

    assert len(manager.managed_branches) == 2