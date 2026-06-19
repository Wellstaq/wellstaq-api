"""
Department model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.department import Department
from app.models.organization import Organization
from app.models.user import User


# ── Factories ──────────────────────────────────────────────────────────────────

def make_user(email: str = "owner@example.com", **overrides) -> User:
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
    defaults = dict(name="Lagos Branch", organization_id=organization_id)
    return Branch(**{**defaults, **overrides})


def make_department(organization_id: str, **overrides) -> Department:
    defaults = dict(
        name="Engineering",
        organization_id=organization_id,
    )
    return Department(**{**defaults, **overrides})


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
):
    user = make_user(email=email)
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=slug)
    db_session.add(org)
    await db_session.flush()

    return user, org


async def setup_branch(db_session: AsyncSession, org_id: str, **overrides) -> Branch:
    branch = make_branch(org_id, **overrides)
    db_session.add(branch)
    await db_session.flush()
    return branch


# ── Department model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_department_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()

    result = await db_session.execute(select(Department).where(Department.id == dept.id))
    fetched = result.scalar_one()
    assert fetched.name == "Engineering"
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_department_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()

    parts = dept.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_department_timestamps_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()

    assert dept.created_at is not None
    assert dept.updated_at is not None


@pytest.mark.asyncio
async def test_department_default_member_count_is_zero(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()

    assert dept.member_count == 0


@pytest.mark.asyncio
async def test_department_optional_fields_nullable(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()

    assert dept.description is None
    assert dept.avatar_initial is None
    assert dept.rank is None
    #assert dept.category_id is None
    assert dept.branch_id is None
    assert dept.created_by is None


@pytest.mark.asyncio
async def test_department_full_fields(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)

    dept = make_department(
        org.id,
        branch_id=branch.id,
        description="Builds the product",
        avatar_initial="EN",
        rank=1,
        created_by=owner.id,
        member_count=5,
    )
    db_session.add(dept)
    await db_session.flush()

    result = await db_session.execute(select(Department).where(Department.id == dept.id))
    fetched = result.scalar_one()
    assert fetched.branch_id == branch.id
    assert fetched.description == "Builds the product"
    assert fetched.avatar_initial == "EN"
    assert fetched.rank == 1
    assert fetched.created_by == owner.id
    assert fetched.member_count == 5


@pytest.mark.asyncio
async def test_department_org_level_has_no_branch(db_session: AsyncSession):
    """branch_id=None means department sits at org level, not under a branch."""
    _, org = await setup_org(db_session)

    dept = make_department(org.id, branch_id=None)
    db_session.add(dept)
    await db_session.flush()

    assert dept.branch_id is None
    assert dept.organization_id == org.id


@pytest.mark.asyncio
async def test_department_org_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    dept = make_department(organization_id="00000000-0000-0000-0000-000000000000")
    db_session.add(dept)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_department_branch_fk_must_exist(db_session: AsyncSession):
    """branch_id must reference a real branch if provided."""
    _, org = await setup_org(db_session)

    dept = make_department(org.id, branch_id="00000000-0000-0000-0000-000000000000")
    db_session.add(dept)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_department_cascades_on_org_delete(db_session: AsyncSession):
    """Deleting an org cascades to its departments."""
    _, org = await setup_org(db_session)

    db_session.add(make_department(org.id, name="Engineering"))
    db_session.add(make_department(org.id, name="HR"))
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(Department).where(Department.organization_id == org.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_department_branch_delete_nullifies_branch_id(db_session: AsyncSession):
    """ondelete=SET NULL — deleting a branch detaches its departments, doesn't delete them."""
    _, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)

    dept = make_department(org.id, branch_id=branch.id)
    db_session.add(dept)
    await db_session.flush()

    await db_session.delete(branch)
    await db_session.flush()
    await db_session.refresh(dept)

    assert dept.branch_id is None       # detached from branch
    assert dept.organization_id == org.id  # still belongs to org


@pytest.mark.asyncio
async def test_org_can_have_multiple_departments(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    db_session.add(make_department(org.id, name="Engineering"))
    db_session.add(make_department(org.id, name="HR"))
    db_session.add(make_department(org.id, name="Finance"))
    await db_session.flush()

    result = await db_session.execute(
        select(Department).where(Department.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_branch_can_have_multiple_departments(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)

    db_session.add(make_department(org.id, name="Engineering", branch_id=branch.id))
    db_session.add(make_department(org.id, name="HR", branch_id=branch.id))
    await db_session.flush()

    result = await db_session.execute(
        select(Department).where(Department.branch_id == branch.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_department_member_count_can_be_updated(db_session: AsyncSession):
    """member_count is updated by app layer — verify it persists correctly."""
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()

    dept.member_count = 10
    await db_session.flush()
    await db_session.refresh(dept)

    assert dept.member_count == 10


@pytest.mark.asyncio
async def test_created_by_is_plain_string_no_fk(db_session: AsyncSession):
    """created_by is an audit string — no FK, preserved even if creator is deleted."""
    owner, org = await setup_org(db_session)

    dept = make_department(org.id, created_by=owner.id)
    db_session.add(dept)
    await db_session.flush()

    # Transfer org ownership before deleting original owner
    new_owner = make_user(email="new_owner@example.com")
    db_session.add(new_owner)
    await db_session.flush()
    org.owner_id = new_owner.id
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"),
        {"id": owner.id}
    )
    await db_session.flush()

    result = await db_session.execute(select(Department).where(Department.id == dept.id))
    fetched = result.scalar_one()
    assert fetched.created_by == owner.id


# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_department_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    dept = make_department(org.id)
    db_session.add(dept)
    await db_session.flush()
    await db_session.refresh(dept, ["organization"])

    assert dept.organization is not None
    assert dept.organization.id == org.id


@pytest.mark.asyncio
async def test_department_branch_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)

    dept = make_department(org.id, branch_id=branch.id)
    db_session.add(dept)
    await db_session.flush()
    await db_session.refresh(dept, ["branch"])

    assert dept.branch is not None
    assert dept.branch.id == branch.id


@pytest.mark.asyncio
async def test_organization_departments_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    db_session.add(make_department(org.id, name="Engineering"))
    db_session.add(make_department(org.id, name="HR"))
    await db_session.flush()
    await db_session.refresh(org, ["departments"])

    assert len(org.departments) == 2


@pytest.mark.asyncio
async def test_branch_departments_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await setup_branch(db_session, org.id)

    db_session.add(make_department(org.id, name="Engineering", branch_id=branch.id))
    db_session.add(make_department(org.id, name="HR", branch_id=branch.id))
    await db_session.flush()
    await db_session.refresh(branch, ["departments"])

    assert len(branch.departments) == 2