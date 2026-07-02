
"""
ClubMember model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Schema change this file reflects:
- Club.branch_id is now NOT NULL — make_club() and setup_club() both
  require a branch. A local make_branch() helper creates one.
- test_club_member_different_clubs_same_user is REMOVED — the
  UniqueConstraint("user_id") on ClubMember means a user can only
  belong to ONE club at a time. That test was asserting the wrong
  behavior. Replaced with test_one_user_cannot_join_two_clubs which
  asserts the constraint fires correctly.
- test_user_club_memberships_relationship is updated to test the swap
  pattern (delete old → insert new) rather than simultaneous membership.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, ClubMember, Organization, User
from app.models.branch import Branch
from tests.conftest import make_org_with_owner, make_user


# ── Factories ──────────────────────────────────────────────────────────────────

async def make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Main Branch",
) -> Branch:
    branch = Branch(organization_id=organization_id, name=name)
    db_session.add(branch)
    await db_session.flush()
    return branch


def make_club(organization_id: str, branch_id: str, **overrides) -> Club:
    defaults = dict(name="Running Club", organization_id=organization_id, branch_id=branch_id)
    return Club(**{**defaults, **overrides})


def make_membership(club_id: str, user_id: str, **overrides) -> ClubMember:
    return ClubMember(club_id=club_id, user_id=user_id, **overrides)


async def setup_club(
    db_session: AsyncSession,
    org_id: str,
    branch_id: str,
    **overrides,
) -> Club:
    club = make_club(org_id, branch_id, **overrides)
    db_session.add(club)
    await db_session.flush()
    return club


# ── ClubMember model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_club_member_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.id == membership.id)
    )
    fetched = result.scalar_one()
    assert fetched.club_id == club.id
    assert fetched.user_id == member.id


@pytest.mark.asyncio
async def test_club_member_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()

    parts = membership.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_club_member_joined_at_auto_set(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()

    assert membership.joined_at is not None


@pytest.mark.asyncio
async def test_club_member_duplicate_raises(db_session: AsyncSession):
    """A user can only join a given club once."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_one_user_cannot_join_two_clubs(db_session: AsyncSession):
    """
    A user can only belong to ONE club at a time.
    UniqueConstraint("user_id") on ClubMember enforces this — attempting
    to insert a second ClubMember row for the same user raises IntegrityError
    regardless of which club they're joining.
    """
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club_a = await setup_club(db_session, org.id, branch.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, branch.id, name="Book Club")

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club_a.id, member.id))
    await db_session.flush()

    db_session.add(make_membership(club_b.id, member.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_can_change_club_via_delete_then_insert(db_session: AsyncSession):
    """
    Changing clubs = DELETE old membership → INSERT new one, atomically.
    After the swap the user belongs to the new club only.
    """
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club_a = await setup_club(db_session, org.id, branch.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, branch.id, name="Book Club")

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    old_membership = make_membership(club_a.id, member.id)
    db_session.add(old_membership)
    await db_session.flush()

    # Delete old — flush releases the unique constraint on user_id
    await db_session.delete(old_membership)
    await db_session.flush()

    # Insert new
    new_membership = make_membership(club_b.id, member.id)
    db_session.add(new_membership)
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.user_id == member.id)
    )
    memberships = result.scalars().all()
    assert len(memberships) == 1
    assert memberships[0].club_id == club_b.id


@pytest.mark.asyncio
async def test_club_can_have_multiple_members(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    m1 = make_user(org.id, email="m1@example.com")
    m2 = make_user(org.id, email="m2@example.com")
    m3 = make_user(org.id, email="m3@example.com")
    db_session.add_all([m1, m2, m3])
    await db_session.flush()

    db_session.add(make_membership(club.id, m1.id))
    db_session.add(make_membership(club.id, m2.id))
    db_session.add(make_membership(club.id, m3.id))
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.club_id == club.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_club_member_club_fk_must_exist(db_session: AsyncSession):
    """club_id must reference a real club."""
    org, _ = await make_org_with_owner(db_session)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(
        club_id="00000000-0000-0000-0000-000000000000",
        user_id=member.id,
    )
    db_session.add(membership)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_member_user_fk_must_exist(db_session: AsyncSession):
    """user_id must reference a real user."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    membership = make_membership(
        club_id=club.id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(membership)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_delete_cascades_to_memberships(db_session: AsyncSession):
    """Deleting a club removes all its membership rows."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    await db_session.flush()

    await db_session.delete(club)
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.club_id == club.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_user_delete_cascades_to_memberships(db_session: AsyncSession):
    """Deleting a user removes all their club membership rows."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"),
        {"id": member.id}
    )
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.user_id == member.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_leaving_club_deletes_row(db_session: AsyncSession):
    """Leaving a club = deleting the membership row."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()

    await db_session.delete(membership)
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(
            ClubMember.club_id == club.id,
            ClubMember.user_id == member.id,
        )
    )
    assert result.scalar_one_or_none() is None


# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_club_member_club_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()
    await db_session.refresh(membership, ["club"])

    assert membership.club is not None
    assert membership.club.id == club.id


@pytest.mark.asyncio
async def test_club_member_user_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()
    await db_session.refresh(membership, ["user"])

    assert membership.user is not None
    assert membership.user.id == member.id


@pytest.mark.asyncio
async def test_club_members_relationship(db_session: AsyncSession):
    """club.members loads all ClubMember rows for that club."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    m1 = make_user(org.id, email="m1@example.com")
    m2 = make_user(org.id, email="m2@example.com")
    db_session.add_all([m1, m2])
    await db_session.flush()

    db_session.add(make_membership(club.id, m1.id))
    db_session.add(make_membership(club.id, m2.id))
    await db_session.flush()
    await db_session.refresh(club, ["members"])

    assert len(club.members) == 2


@pytest.mark.asyncio
async def test_user_club_memberships_relationship_after_swap(db_session: AsyncSession):
    """
    After swapping clubs (delete old → insert new), user.club_memberships
    must contain exactly one row pointing to the new club.
    """
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club_a = await setup_club(db_session, org.id, branch.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, branch.id, name="Book Club")

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    old = make_membership(club_a.id, member.id)
    db_session.add(old)
    await db_session.flush()

    await db_session.delete(old)
    await db_session.flush()

    db_session.add(make_membership(club_b.id, member.id))
    await db_session.flush()

    await db_session.refresh(member, ["club_memberships"])
    assert len(member.club_memberships) == 1
    assert member.club_memberships[0].club_id == club_b.id
