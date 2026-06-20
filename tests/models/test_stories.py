"""
Story model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Story, User


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


def make_story(user_id: str, organization_id: str, **overrides) -> Story:
    now = datetime.now(timezone.utc)
    defaults = dict(
        media_url="https://cdn.example.com/story.jpg",
        media_type="image",
        expires_at=now + timedelta(hours=24),
    )
    return Story(
        user_id=user_id,
        organization_id=organization_id,
        **{**defaults, **overrides},
    )


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


# ── Creation / required fields ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_story_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    story = make_story(owner.id, org.id)
    db_session.add(story)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story.id))
    fetched = result.scalar_one()
    assert fetched.media_url == "https://cdn.example.com/story.jpg"
    assert fetched.media_type == "image"
    assert fetched.user_id == owner.id
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_story_id_is_uuid_format(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id)
    db_session.add(story)
    await db_session.flush()

    parts = story.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_story_media_type_defaults_to_image(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = Story(
        user_id=owner.id,
        organization_id=org.id,
        media_url="https://cdn.example.com/default.jpg",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(story)
    await db_session.flush()

    assert story.media_type == "image"


@pytest.mark.asyncio
async def test_story_supports_video(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(
        owner.id, org.id,
        media_url="https://cdn.example.com/clip.mp4",
        media_type="video",
    )
    db_session.add(story)
    await db_session.flush()

    assert story.media_type == "video"


@pytest.mark.asyncio
async def test_story_user_id_required(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = Story(
        user_id=None,
        organization_id=org.id,
        media_url="https://cdn.example.com/x.jpg",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(story)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_story_organization_id_required(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = Story(
        user_id=owner.id,
        organization_id=None,
        media_url="https://cdn.example.com/x.jpg",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(story)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── FK constraints ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_delete_cascades_to_stories(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    poster = make_user(email="poster@example.com")
    db_session.add(poster)
    await db_session.flush()

    story = make_story(poster.id, org.id)
    db_session.add(story)
    await db_session.flush()
    story_id = story.id

    await db_session.delete(poster)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_stories(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id)
    db_session.add(story)
    await db_session.flush()
    story_id = story.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story_id))
    assert result.scalar_one_or_none() is None


# ── Relationships ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_story_user_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id)
    db_session.add(story)
    await db_session.flush()
    await db_session.refresh(story, ["user"])

    assert story.user is not None
    assert story.user.id == owner.id


@pytest.mark.asyncio
async def test_story_organization_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id)
    db_session.add(story)
    await db_session.flush()
    await db_session.refresh(story, ["organization"])

    assert story.organization is not None
    assert story.organization.id == org.id


@pytest.mark.asyncio
async def test_user_stories_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    db_session.add(make_story(owner.id, org.id, media_url="https://cdn.example.com/1.jpg"))
    db_session.add(make_story(owner.id, org.id, media_url="https://cdn.example.com/2.jpg"))
    await db_session.flush()
    await db_session.refresh(owner, ["stories"])

    assert len(owner.stories) == 2


@pytest.mark.asyncio
async def test_organization_stories_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)

    poster2 = make_user(email="poster2@example.com")
    db_session.add(poster2)
    await db_session.flush()

    db_session.add(make_story(owner.id, org.id))
    db_session.add(make_story(poster2.id, org.id))
    await db_session.flush()
    await db_session.refresh(org, ["stories"])

    assert len(org.stories) == 2


# ── Expiry behavior ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expired_story_still_queryable_until_purged(db_session: AsyncSession):
    """Expiry is enforced by a background purge job, not a query filter —
    an expired row still exists in the table until that job runs."""
    owner, org = await setup_org(db_session)
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    story = make_story(owner.id, org.id, expires_at=past)
    db_session.add(story)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story.id))
    fetched = result.scalar_one()
    assert fetched.expires_at < datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_purge_query_deletes_only_expired_stories(db_session: AsyncSession):
    """Sanity check on the purge query logic itself (see story.py's
    purge_expired_stories reference) — only expired rows are removed,
    active ones survive."""
    owner, org = await setup_org(db_session)
    now = datetime.now(timezone.utc)

    expired = make_story(owner.id, org.id, expires_at=now - timedelta(hours=1))
    active = make_story(owner.id, org.id, expires_at=now + timedelta(hours=23))
    db_session.add_all([expired, active])
    await db_session.flush()
    active_id = active.id

    from sqlalchemy import delete
    await db_session.execute(delete(Story).where(Story.expires_at <= now))
    await db_session.flush()

    result = await db_session.execute(select(Story))
    remaining = result.scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == active_id


# ── Indexes ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_expires_at_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('stories')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_stories_user_expires_at" in index_names