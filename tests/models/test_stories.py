"""
Story model tests.

branch_id is NULLABLE on Story — a user chooses between posting to their
branch feed (branch_id set) or the org-wide feed (branch_id null).
This mirrors Post.branch_id.

make_story() accepts an optional branch_id (default None = org-level story).
Tests that explicitly need a branch-scoped story pass owner.branch_id.

Branch deletion uses SET NULL on Story.branch_id — the story survives
as an org-level story; it is NOT cascade-deleted.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Story, User
from app.models.branch import Branch
from tests.conftest import make_user, make_org_with_owner


# ── Factories ──────────────────────────────────────────────────────────────────

def make_story(
    user_id: str,
    organization_id: str,
    *,
    branch_id: str | None = None,
    **overrides,
) -> Story:
    """
    branch_id is optional — pass it for a branch-scoped story,
    omit it (or pass None) for an org-level story.
    """
    now = datetime.now(timezone.utc)
    defaults = dict(
        media_url="https://cdn.example.com/story.jpg",
        media_type="image",
        expires_at=now + timedelta(hours=24),
    )
    return Story(
        user_id=user_id,
        organization_id=organization_id,
        branch_id=branch_id,
        **{**defaults, **overrides},
    )


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


# ── Creation / required fields ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_level_story_persists(db_session: AsyncSession):
    """branch_id=None creates an org-level story (no branch scope)."""
    owner, org = await setup_org(db_session)

    story = make_story(owner.id, org.id)  # no branch_id
    db_session.add(story)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story.id))
    fetched = result.scalar_one()
    assert fetched.media_url == "https://cdn.example.com/story.jpg"
    assert fetched.media_type == "image"
    assert fetched.user_id == owner.id
    assert fetched.organization_id == org.id
    assert fetched.branch_id is None


@pytest.mark.asyncio
async def test_branch_level_story_persists(db_session: AsyncSession):
    """branch_id set creates a branch-scoped story."""
    owner, org = await setup_org(db_session)

    story = make_story(owner.id, org.id, branch_id=owner.branch_id)
    db_session.add(story)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story.id))
    fetched = result.scalar_one()
    assert fetched.branch_id == owner.branch_id


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
async def test_story_timestamps_auto_set(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id)
    db_session.add(story)
    await db_session.flush()

    assert story.created_at is not None
    assert story.updated_at is not None


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


@pytest.mark.asyncio
async def test_story_branch_id_is_nullable(db_session: AsyncSession):
    """branch_id=None is valid — that's an org-level story."""
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id, branch_id=None)
    db_session.add(story)
    await db_session.flush()  # must not raise

    result = await db_session.execute(select(Story).where(Story.id == story.id))
    assert result.scalar_one().branch_id is None


@pytest.mark.asyncio
async def test_story_branch_fk_must_exist_when_set(db_session: AsyncSession):
    """If branch_id is provided it must reference a real branch."""
    owner, org = await setup_org(db_session)
    story = make_story(
        owner.id, org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(story)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── FK constraints ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_delete_cascades_to_stories(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    poster = make_user(organization_id=org.id, email="poster@example.com", branch_id=owner.branch_id)
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

@pytest.mark.asyncio
async def test_branch_delete_sets_story_branch_id_to_null(db_session: AsyncSession):
    """
    branch_id uses ondelete=SET NULL — deleting a branch does NOT delete
    its stories. They survive as org-level stories (branch_id → null).

    Uses a secondary branch (no members) so the branch deletion doesn't
    cascade through users to stories. The owner posts a story scoped to
    that branch; after deletion the story survives with branch_id=None.
    """
    owner, org = await setup_org(db_session)

    # Create a separate branch with no members so deleting it doesn't
    # cascade through User.branch_id → Story.user_id → story deleted.
    other_branch = Branch(
        organization_id=org.id,
        name="Other Branch",
        created_by=owner.id,
    )
    db_session.add(other_branch)
    await db_session.flush()

    story = make_story(owner.id, org.id, branch_id=other_branch.id)
    db_session.add(story)
    await db_session.flush()
    story_id = story.id

    await db_session.delete(other_branch)
    await db_session.flush()
    db_session.expire_all()  # ← force re-fetch from DB on next access

    result = await db_session.execute(select(Story).where(Story.id == story_id))
    surviving = result.scalar_one_or_none()
    assert surviving is not None
    assert surviving.branch_id is None

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
async def test_story_branch_relationship_when_set(db_session: AsyncSession):
    """story.branch loads the branch when branch_id is set."""
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id, branch_id=owner.branch_id)
    db_session.add(story)
    await db_session.flush()
    await db_session.refresh(story, ["branch"])

    assert story.branch is not None
    assert story.branch.id == owner.branch_id


@pytest.mark.asyncio
async def test_story_branch_relationship_is_none_for_org_level(db_session: AsyncSession):
    """story.branch is None when branch_id is not set."""
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id)  # no branch_id
    db_session.add(story)
    await db_session.flush()
    await db_session.refresh(story, ["branch"])

    assert story.branch is None


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
    poster2 = make_user(organization_id=org.id, email="poster2@example.com", branch_id=owner.branch_id)
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
    """Expiry is enforced by a background purge job — expired rows still
    exist in the table until that job runs."""
    owner, org = await setup_org(db_session)
    soon_after = datetime.now(timezone.utc) + timedelta(seconds=1)

    story = make_story(owner.id, org.id, expires_at=soon_after)
    db_session.add(story)
    await db_session.flush()

    result = await db_session.execute(select(Story).where(Story.id == story.id))
    assert result.scalar_one().expires_at == soon_after


@pytest.mark.asyncio
async def test_purge_query_deletes_only_expired_stories(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    now = datetime.now(timezone.utc)

    expired = make_story(owner.id, org.id, expires_at=now + timedelta(seconds=1))
    active = make_story(owner.id, org.id, expires_at=now + timedelta(hours=23))
    db_session.add_all([expired, active])
    await db_session.flush()
    active_id = active.id
    cutoff = now + timedelta(seconds=2)

    await db_session.execute(delete(Story).where(Story.expires_at <= cutoff))
    await db_session.flush()

    result = await db_session.execute(select(Story))
    remaining = result.scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == active_id


# ── CheckConstraints ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_story_invalid_media_type_raises(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(owner.id, org.id, media_type="audio")
    db_session.add(story)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_story_expires_at_before_created_at_raises(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    story = make_story(
        owner.id, org.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(story)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Indexes ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_expires_at_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('stories')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_stories_user_expires_at" in index_names


@pytest.mark.asyncio
async def test_org_expires_at_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('stories')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_stories_org_expires_at" in index_names