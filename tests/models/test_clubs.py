"""
Club model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Factory note:
make_user() and make_org_with_owner() are imported from conftest.py —
the single shared implementation that resolves the circular FK between
Organization.owner_id and User.organization_id. The local setup_org()
is a thin wrapper that preserves this file's (user, org) return order
and (email, slug) call signature so no test body needed to change.
Any additional users (new_owner, members) are created via
make_user(organization_id=org.id, ...) since every User row must
belong to an org.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Organization, User
from tests.conftest import make_user, make_org_with_owner


# ── Factories ──────────────────────────────────────────────────────────────────

def make_club(organization_id: str, **overrides) -> Club:
    defaults = dict(
        name="Running Club",
        organization_id=organization_id,
    )
    return Club(**{**defaults, **overrides})


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
) -> tuple[User, Organization]:
    """Thin wrapper around the shared make_org_with_owner() factory.
    Preserves this file's original (user, org) return order and
    (email, slug) signature so existing call sites didn't need to change."""
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

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    result = await db_session.execute(select(Club).where(Club.id == club.id))
    fetched = result.scalar_one()
    assert fetched.name == "Running Club"
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_club_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    parts = club.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_club_timestamps_auto_set(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    assert club.created_at is not None
    assert club.updated_at is not None


@pytest.mark.asyncio
async def test_club_default_privacy_is_public(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    assert club.privacy == "public"


@pytest.mark.asyncio
async def test_club_default_member_count_is_zero(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    assert club.member_count == 0


@pytest.mark.asyncio
async def test_club_optional_fields_nullable(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    assert club.description is None
    assert club.created_by is None


@pytest.mark.asyncio
async def test_club_full_fields(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    club = make_club(
        org.id,
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
    _, org = await setup_org(db_session, email=f"{privacy}@example.com", slug=f"org-{privacy}")

    club = make_club(org.id, privacy=privacy)
    db_session.add(club)
    await db_session.flush()

    assert club.privacy == privacy


@pytest.mark.asyncio
async def test_club_has_no_branch_id(db_session: AsyncSession):
    """Clubs are intentionally org-wide — no branch_id column exists."""
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    assert not hasattr(club, "branch_id")


@pytest.mark.asyncio
async def test_club_org_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    club = make_club(organization_id="00000000-0000-0000-0000-000000000000")
    db_session.add(club)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_club_cascades_on_org_delete(db_session: AsyncSession):
    """Deleting an org cascades to its clubs."""
    _, org = await setup_org(db_session)

    db_session.add(make_club(org.id, name="Running Club"))
    db_session.add(make_club(org.id, name="Book Club"))
    await db_session.flush()

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(Club).where(Club.organization_id == org.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_org_can_have_multiple_clubs(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    db_session.add(make_club(org.id, name="Running Club"))
    db_session.add(make_club(org.id, name="Book Club"))
    db_session.add(make_club(org.id, name="Chess Club"))
    await db_session.flush()

    result = await db_session.execute(
        select(Club).where(Club.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_club_member_count_can_be_updated(db_session: AsyncSession):
    """member_count is updated by app layer — verify it persists correctly."""
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    club.member_count = 25
    await db_session.flush()
    await db_session.refresh(club)

    assert club.member_count == 25


@pytest.mark.asyncio
async def test_created_by_is_plain_string_no_fk(db_session: AsyncSession):
    """created_by is an audit string — no FK, preserved even if creator
    is deleted. The value survives because there's no FK constraint to
    cascade or nullify it — intentional for audit trail purposes.

    new_owner must belong to an org (User.organization_id NOT NULL), so
    it's created in the same org before being assigned as the new
    org.owner_id, allowing the raw deletion of the original owner to
    succeed without hitting the Organization.owner_id FK constraint."""
    owner, org = await setup_org(db_session)

    club = make_club(org.id, created_by=owner.id)
    db_session.add(club)
    await db_session.flush()

    # new_owner must have organization_id — assign it to the same org
    new_owner = make_user(organization_id=org.id, email="new_owner@example.com")
    db_session.add(new_owner)
    await db_session.flush()

    # Transfer ownership so the Organization.owner_id FK no longer
    # blocks deletion of the original owner
    org.owner_id = new_owner.id
    await db_session.flush()

    # Raw delete bypasses ORM to test that Club.created_by (a plain
    # string, no FK) survives independently of the user row
    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"),
        {"id": owner.id}
    )
    await db_session.flush()

    result = await db_session.execute(select(Club).where(Club.id == club.id))
    fetched = result.scalar_one()
    assert fetched.created_by == owner.id


# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_club_organization_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()
    await db_session.refresh(club, ["organization"])

    assert club.organization is not None
    assert club.organization.id == org.id


@pytest.mark.asyncio
async def test_organization_clubs_relationship(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    db_session.add(make_club(org.id, name="Running Club"))
    db_session.add(make_club(org.id, name="Book Club"))
    await db_session.flush()
    await db_session.refresh(org, ["clubs"])

    assert len(org.clubs) == 2
    names = {c.name for c in org.clubs}
    assert names == {"Running Club", "Book Club"}