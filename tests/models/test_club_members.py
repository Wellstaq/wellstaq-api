"""
ClubMember model tests.

REVISED for two separate issues:

1. branch_id NOT NULL (User model): every make_user() call now passes
   branch_id — using the org's already-created test branch, since
   these tests aren't about branch assignment specifically.

2. STALE CONSTRAINT ASSUMPTION: this file previously assumed
   ClubMember has UniqueConstraint("user_id") — i.e. a user can only
   belong to ONE club at a time, requiring a delete-then-insert swap
   to change clubs. That is NOT what the current Club model has.
   ClubMember.__table_args__ only has
   UniqueConstraint("club_id", "user_id", ...) — a user can belong to
   MANY clubs simultaneously; only re-joining the SAME club twice is
   blocked. This matches join_club()/leave_club()'s own docstrings
   elsewhere in the codebase ("A user can belong to multiple clubs
   simultaneously"). The old test_one_user_cannot_join_two_clubs,
   test_user_can_change_club_via_delete_then_insert, and
   test_user_club_memberships_relationship_after_swap all asserted the
   wrong (single-club) behavior and have been replaced below with
   tests asserting the actual (multi-club) behavior.
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()

    assert membership.joined_at is not None


@pytest.mark.asyncio
async def test_club_member_duplicate_raises(db_session: AsyncSession):
    """A user can only join a given club once — UniqueConstraint(club_id, user_id)."""
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club = await setup_club(db_session, org.id, branch.id)

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_one_user_can_join_multiple_clubs(db_session: AsyncSession):
    """
    REPLACES test_one_user_cannot_join_two_clubs (which asserted the
    wrong, now-nonexistent constraint). The current model only has
    UniqueConstraint(club_id, user_id) — a user CAN belong to many
    clubs at once; only re-joining the SAME club twice is blocked.
    """
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club_a = await setup_club(db_session, org.id, branch.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, branch.id, name="Book Club")

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club_a.id, member.id))
    db_session.add(make_membership(club_b.id, member.id))
    await db_session.flush()  # must NOT raise

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.user_id == member.id)
    )
    memberships = result.scalars().all()
    assert len(memberships) == 2
    assert {m.club_id for m in memberships} == {club_a.id, club_b.id}


@pytest.mark.asyncio
async def test_user_can_leave_one_club_while_staying_in_another(db_session: AsyncSession):
    """
    REPLACES test_user_can_change_club_via_delete_then_insert, which
    described a swap workflow that only made sense under the old
    one-club-at-a-time constraint. Under the current model, leaving
    one club is just deleting that one ClubMember row — it has no
    effect on the user's other memberships.
    """
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club_a = await setup_club(db_session, org.id, branch.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, branch.id, name="Book Club")

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    membership_a = make_membership(club_a.id, member.id)
    membership_b = make_membership(club_b.id, member.id)
    db_session.add_all([membership_a, membership_b])
    await db_session.flush()

    await db_session.delete(membership_a)
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

    m1 = make_user(org.id, email="m1@example.com", branch_id=branch.id)
    m2 = make_user(org.id, email="m2@example.com", branch_id=branch.id)
    m3 = make_user(org.id, email="m3@example.com", branch_id=branch.id)
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
    branch = await make_branch(db_session, org.id)

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
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

    m1 = make_user(org.id, email="m1@example.com", branch_id=branch.id)
    m2 = make_user(org.id, email="m2@example.com", branch_id=branch.id)
    db_session.add_all([m1, m2])
    await db_session.flush()

    db_session.add(make_membership(club.id, m1.id))
    db_session.add(make_membership(club.id, m2.id))
    await db_session.flush()
    await db_session.refresh(club, ["members"])

    assert len(club.members) == 2


@pytest.mark.asyncio
async def test_user_club_memberships_relationship_shows_all_clubs(db_session: AsyncSession):
    """
    REPLACES test_user_club_memberships_relationship_after_swap.
    A user belonging to multiple clubs simultaneously must see ALL of
    them via user.club_memberships — not just the most recent one.
    """
    org, _ = await make_org_with_owner(db_session)
    branch = await make_branch(db_session, org.id)
    club_a = await setup_club(db_session, org.id, branch.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, branch.id, name="Book Club")

    member = make_user(org.id, email="member@example.com", branch_id=branch.id)
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club_a.id, member.id))
    db_session.add(make_membership(club_b.id, member.id))
    await db_session.flush()

    await db_session.refresh(member, ["club_memberships"])
    assert len(member.club_memberships) == 2
    assert {m.club_id for m in member.club_memberships} == {club_a.id, club_b.id}