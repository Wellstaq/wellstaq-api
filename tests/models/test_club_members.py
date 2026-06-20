"""
ClubMember model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, ClubMember
from app.models import Organization
from app.models import User


# ── Factories ──────────────────────────────────────────────────────────────────

def make_user(email: str = "user@example.com", **overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        role_name="employee",
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


def make_club(organization_id: str, **overrides) -> Club:
    defaults = dict(name="Running Club", organization_id=organization_id)
    return Club(**{**defaults, **overrides})


def make_membership(club_id: str, user_id: str, **overrides) -> ClubMember:
    return ClubMember(club_id=club_id, user_id=user_id, **overrides)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
):
    user = make_user(email=email, role_name="overall_manager")
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=slug)
    db_session.add(org)
    await db_session.flush()

    return user, org


async def setup_club(db_session: AsyncSession, org_id: str, **overrides) -> Club:
    club = make_club(org_id, **overrides)
    db_session.add(club)
    await db_session.flush()
    return club


# ── ClubMember model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_club_member_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    membership = make_membership(club.id, member.id)
    db_session.add(membership)
    await db_session.flush()

    assert membership.joined_at is not None


@pytest.mark.asyncio
async def test_club_member_duplicate_raises(db_session: AsyncSession):
    """A user can only join a given club once."""
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    await db_session.flush()

    db_session.add(make_membership(club.id, member.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_member_different_clubs_same_user(db_session: AsyncSession):
    """A user can join multiple different clubs."""
    _, org = await setup_org(db_session)
    club_a = await setup_club(db_session, org.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, name="Book Club")

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club_a.id, member.id))
    db_session.add(make_membership(club_b.id, member.id))
    await db_session.flush()

    result = await db_session.execute(
        select(ClubMember).where(ClubMember.user_id == member.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_club_can_have_multiple_members(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    m1 = make_user(email="m1@example.com")
    m2 = make_user(email="m2@example.com")
    m3 = make_user(email="m3@example.com")
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
    _, org = await setup_org(db_session)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    member = make_user(email="member@example.com")
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    m1 = make_user(email="m1@example.com")
    m2 = make_user(email="m2@example.com")
    db_session.add_all([m1, m2])
    await db_session.flush()

    db_session.add(make_membership(club.id, m1.id))
    db_session.add(make_membership(club.id, m2.id))
    await db_session.flush()
    await db_session.refresh(club, ["members"])

    assert len(club.members) == 2


@pytest.mark.asyncio
async def test_user_club_memberships_relationship(db_session: AsyncSession):
    """user.club_memberships loads all ClubMember rows for that user."""
    _, org = await setup_org(db_session)
    club_a = await setup_club(db_session, org.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, name="Book Club")

    member = make_user(email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_membership(club_a.id, member.id))
    db_session.add(make_membership(club_b.id, member.id))
    await db_session.flush()
    await db_session.refresh(member, ["club_memberships"])

    assert len(member.club_memberships) == 2