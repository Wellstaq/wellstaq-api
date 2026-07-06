"""
Club model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Schema change this file reflects:
- Club.branch_id is now NOT NULL — every club must belong to exactly
  one branch. make_club() now requires branch_id. All tests that
  create a Club must first create a Branch.
- test_club_has_no_branch_id has been replaced with
  test_club_branch_id_is_required, which asserts the opposite.
- New cascade test: deleting a branch cascades to its clubs.
- New relationship tests: Club.branch and Branch.clubs.
- UniqueConstraint("organization_id", "name") added — two clubs in
  the same org cannot share a name, even across branches.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Organization, User
from app.models.branch import Branch
from tests.conftest import make_user, make_org_with_owner


# ── Factories ──────────────────────────────────────────────────────────────────

def make_club(organization_id: str, branch_id: str, **overrides) -> Club:
    """
    branch_id is required — every club must belong to exactly one branch.
    """
    defaults = dict(
        name="Running Club",
        organization_id=organization_id,
        branch_id=branch_id,
    )
    return Club(**{**defaults, **overrides})


async def make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Main Branch",
) -> Branch:
    branch = Branch(organization_id=organization_id, name=name)
    db_session.add(branch)
    await db_session.flush()
    return branch


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
) -> tuple[User, Organization]:
    org, user = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=slug),
        user_kwargs=dict(email=email, role_name="overall_manager"),
    )
    return user, org


# ── Club model ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_club_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    result = await db_session.execute(select(Club).where(Club.id == club.id))
    fetched = result.scalar_one()
    assert fetched.name == "Running Club"
    assert fetched.organization_id == org.id
    assert fetched.branch_id == branch.id


@pytest.mark.asyncio
async def test_club_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    parts = club.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_club_timestamps_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    assert club.created_at is not None
    assert club.updated_at is not None


@pytest.mark.asyncio
async def test_club_default_privacy_is_public(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    assert club.privacy == "public"


@pytest.mark.asyncio
async def test_club_default_member_count_is_zero(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    assert club.member_count == 0


@pytest.mark.asyncio
async def test_club_optional_fields_nullable(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    assert club.description is None
    assert club.created_by is None


@pytest.mark.asyncio
async def test_club_full_fields(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(
        org.id,
        branch.id,
        description="Weekly running group",
        privacy="private",
        created_by=owner.id,
        member_count=12,
    )
    db_session.add(club)
    await db_session.flush()

    result = await db_session.execute(select(Club).where(Club.id == club.id))
    fetched = result.scalar_one()
    assert fetched.description == "Weekly running group"
    assert fetched.privacy == "private"
    assert fetched.created_by == owner.id
    assert fetched.member_count == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy", ["public", "private"])
async def test_club_accepts_any_privacy_value(db_session: AsyncSession, privacy: str):
    """privacy is a free string — no enum constraint at DB level."""
    _, org = await setup_org(
        db_session, email=f"{privacy}@example.com", slug=f"org-{privacy}"
    )
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id, privacy=privacy)
    db_session.add(club)
    await db_session.flush()

    assert club.privacy == privacy


@pytest.mark.asyncio
async def test_club_branch_id_is_required(db_session: AsyncSession):
    """branch_id is NOT NULL — a Club cannot be created without a branch."""
    _, org = await setup_org(db_session)

    club = Club(name="No Branch Club", organization_id=org.id)  # branch_id omitted
    db_session.add(club)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_branch_fk_must_exist(db_session: AsyncSession):
    """branch_id must reference a real branch."""
    _, org = await setup_org(db_session)

    club = make_club(
        org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(club)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_org_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(
        organization_id="00000000-0000-0000-0000-000000000000",
        branch_id=branch.id,
    )
    db_session.add(club)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_name_unique_within_org(db_session: AsyncSession):
    """Two clubs in the same org cannot share a name, even across branches."""
    _, org = await setup_org(db_session)
    branch1 = await make_branch(db_session, org.id, name="Branch A")
    branch2 = await make_branch(db_session, org.id, name="Branch B")

    db_session.add(make_club(org.id, branch1.id, name="Running Club"))
    await db_session.flush()

    db_session.add(make_club(org.id, branch2.id, name="Running Club"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_same_name_allowed_in_different_orgs(db_session: AsyncSession):
    """Same club name in different orgs is fine — uniqueness is org-scoped."""
    _, org1 = await setup_org(db_session, email="o1@example.com", slug="org-one")
    _, org2 = await setup_org(db_session, email="o2@example.com", slug="org-two")
    branch1 = await make_branch(db_session, org1.id)
    branch2 = await make_branch(db_session, org2.id)

    db_session.add(make_club(org1.id, branch1.id, name="Running Club"))
    db_session.add(make_club(org2.id, branch2.id, name="Running Club"))
    await db_session.flush()  # should not raise


@pytest.mark.asyncio
async def test_club_cascades_on_org_delete(db_session: AsyncSession):
    """Deleting an org cascades to its clubs."""
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    db_session.add(make_club(org.id, branch.id, name="Running Club"))
    db_session.add(make_club(org.id, branch.id, name="Book Club"))
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(Club).where(Club.organization_id == org.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_club_cascades_on_branch_delete(db_session: AsyncSession):
    """Deleting a branch cascades to all clubs that belong to it."""
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    db_session.add(make_club(org.id, branch.id, name="Running Club"))
    db_session.add(make_club(org.id, branch.id, name="Book Club"))
    await db_session.flush()

    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(
        select(Club).where(Club.branch_id == branch.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_branch_does_not_delete_other_branch_clubs(
    db_session: AsyncSession,
):
    """Only the deleted branch's clubs are removed — sibling branches unaffected."""
    _, org = await setup_org(db_session)
    branch1 = await make_branch(db_session, org.id, name="Branch A")
    branch2 = await make_branch(db_session, org.id, name="Branch B")

    db_session.add(make_club(org.id, branch1.id, name="Running Club"))
    surviving_club = make_club(org.id, branch2.id, name="Chess Club")
    db_session.add(surviving_club)
    await db_session.flush()

    await db_session.delete(branch1)
    await db_session.flush()

    result = await db_session.execute(
        select(Club).where(Club.id == surviving_club.id)
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_org_can_have_multiple_clubs_across_branches(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch1 = await make_branch(db_session, org.id, name="Branch A")
    branch2 = await make_branch(db_session, org.id, name="Branch B")

    db_session.add(make_club(org.id, branch1.id, name="Running Club"))
    db_session.add(make_club(org.id, branch1.id, name="Book Club"))
    db_session.add(make_club(org.id, branch2.id, name="Chess Club"))
    await db_session.flush()

    result = await db_session.execute(
        select(Club).where(Club.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_club_member_count_can_be_updated(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()

    club.member_count = 25
    await db_session.flush()
    await db_session.refresh(club)

    assert club.member_count == 25

@pytest.mark.asyncio
async def test_created_by_is_plain_string_no_fk(db_session: AsyncSession):
    """created_by is an audit string — no FK, preserved even if creator
    is deleted."""
    owner, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id, created_by=owner.id)
    db_session.add(club)
    await db_session.flush()

    new_owner = make_user(
        organization_id=org.id,
        email="new_owner@example.com",
        branch_id=owner.branch_id,   # ← added; branch_id is NOT NULL now
    )
    db_session.add(new_owner)
    await db_session.flush()

    org.owner_id = new_owner.id
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"), {"id": owner.id}
    )
    await db_session.flush()

    result = await db_session.execute(select(Club).where(Club.id == club.id))
    fetched = result.scalar_one()
    assert fetched.created_by == owner.id
    
# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_club_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()
    await db_session.refresh(club, ["organization"])

    assert club.organization is not None
    assert club.organization.id == org.id


@pytest.mark.asyncio
async def test_club_branch_relationship(db_session: AsyncSession):
    """club.branch loads the branch the club belongs to."""
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    club = make_club(org.id, branch.id)
    db_session.add(club)
    await db_session.flush()
    await db_session.refresh(club, ["branch"])

    assert club.branch is not None
    assert club.branch.id == branch.id


@pytest.mark.asyncio
async def test_branch_clubs_relationship(db_session: AsyncSession):
    """branch.clubs loads all clubs belonging to the branch."""
    _, org = await setup_org(db_session)
    branch = await make_branch(db_session, org.id)

    db_session.add(make_club(org.id, branch.id, name="Running Club"))
    db_session.add(make_club(org.id, branch.id, name="Book Club"))
    await db_session.flush()
    await db_session.refresh(branch, ["clubs"])

    assert len(branch.clubs) == 2
    names = {c.name for c in branch.clubs}
    assert names == {"Running Club", "Book Club"}


@pytest.mark.asyncio
async def test_organization_clubs_relationship(db_session: AsyncSession):
    """org.clubs loads all clubs across all branches in the org."""
    _, org = await setup_org(db_session)
    branch1 = await make_branch(db_session, org.id, name="Branch A")
    branch2 = await make_branch(db_session, org.id, name="Branch B")

    db_session.add(make_club(org.id, branch1.id, name="Running Club"))
    db_session.add(make_club(org.id, branch2.id, name="Book Club"))
    await db_session.flush()
    await db_session.refresh(org, ["clubs"])

    assert len(org.clubs) == 2
    names = {c.name for c in org.clubs}
    assert names == {"Running Club", "Book Club"}