"""
Post / PostHashtag / PostLike / PostComment / PostShare / PostSave model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    Hashtag,
    Organization,
    Post,
    PostComment,
    PostHashtag,
    PostLike,
    PostSave,
    PostShare,
    User,
)

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


def make_post(user_id: str, organization_id: str, **overrides) -> Post:
    defaults = dict(
        user_id=user_id,
        organization_id=organization_id,
        content="Just hit a new PR!",
    )
    return Post(**{**defaults, **overrides})


def make_hashtag(tag_name: str = "fitness", **overrides) -> Hashtag:
    defaults = dict(tag_name=tag_name, total_post_count=0)
    return Hashtag(**{**defaults, **overrides})


def make_post_like(post_id: str, user_id: str, **overrides) -> PostLike:
    return PostLike(post_id=post_id, user_id=user_id, **overrides)


def make_post_comment(post_id: str, user_id: str, **overrides) -> PostComment:
    defaults = dict(content="Nice work!")
    return PostComment(post_id=post_id, user_id=user_id, **{**defaults, **overrides})


def make_post_share(post_id: str, user_id: str, **overrides) -> PostShare:
    return PostShare(post_id=post_id, user_id=user_id, **overrides)


def make_post_save(post_id: str, user_id: str, **overrides) -> PostSave:
    return PostSave(post_id=post_id, user_id=user_id, **overrides)


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


async def setup_post(db_session: AsyncSession, user_id: str, org_id: str, **overrides) -> Post:
    post = make_post(user_id, org_id, **overrides)
    db_session.add(post)
    await db_session.flush()
    return post


# ── Post model ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    result = await db_session.execute(select(Post).where(Post.id == post.id))
    fetched = result.scalar_one()
    assert fetched.content == "Just hit a new PR!"
    assert fetched.organization_id == org.id
    assert fetched.branch_id is None
    assert fetched.club_id is None


@pytest.mark.asyncio
async def test_post_id_is_uuid_format(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    parts = post.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_post_counters_default_to_zero(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    assert post.like_count == 0
    assert post.comment_count == 0


@pytest.mark.asyncio
async def test_post_can_belong_to_club(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    club = make_club(org.id)
    db_session.add(club)
    await db_session.flush()

    post = await setup_post(db_session, owner.id, org.id, club_id=club.id)

    assert post.club_id == club.id


# ── PostHashtag model ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_hashtag_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    hashtag = make_hashtag()
    db_session.add(hashtag)
    await db_session.flush()

    post_hashtag = PostHashtag(post_id=post.id, hashtag_id=hashtag.id)
    db_session.add(post_hashtag)
    await db_session.flush()

    result = await db_session.execute(
        select(PostHashtag).where(PostHashtag.id == post_hashtag.id)
    )
    fetched = result.scalar_one()
    assert fetched.post_id == post.id
    assert fetched.hashtag_id == hashtag.id


@pytest.mark.asyncio
async def test_post_can_have_multiple_hashtags(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    tag_a = make_hashtag(tag_name="running")
    tag_b = make_hashtag(tag_name="motivation")
    db_session.add_all([tag_a, tag_b])
    await db_session.flush()

    db_session.add(PostHashtag(post_id=post.id, hashtag_id=tag_a.id))
    db_session.add(PostHashtag(post_id=post.id, hashtag_id=tag_b.id))
    await db_session.flush()

    result = await db_session.execute(
        select(PostHashtag).where(PostHashtag.post_id == post.id)
    )
    assert len(result.scalars().all()) == 2


# ── PostLike model ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_like_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    liker = make_user(email="liker@example.com")
    db_session.add(liker)
    await db_session.flush()

    like = make_post_like(post.id, liker.id)
    db_session.add(like)
    await db_session.flush()

    result = await db_session.execute(select(PostLike).where(PostLike.id == like.id))
    fetched = result.scalar_one()
    assert fetched.post_id == post.id
    assert fetched.user_id == liker.id


@pytest.mark.asyncio
async def test_post_like_duplicate_raises(db_session: AsyncSession):
    """A user can only like a given post once."""
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    liker = make_user(email="liker@example.com")
    db_session.add(liker)
    await db_session.flush()

    db_session.add(make_post_like(post.id, liker.id))
    await db_session.flush()

    db_session.add(make_post_like(post.id, liker.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_post_like_same_user_different_posts(db_session: AsyncSession):
    """A user can like multiple different posts."""
    owner, org = await setup_org(db_session)
    post_a = await setup_post(db_session, owner.id, org.id, content="Post A")
    post_b = await setup_post(db_session, owner.id, org.id, content="Post B")

    liker = make_user(email="liker@example.com")
    db_session.add(liker)
    await db_session.flush()

    db_session.add(make_post_like(post_a.id, liker.id))
    db_session.add(make_post_like(post_b.id, liker.id))
    await db_session.flush()

    result = await db_session.execute(
        select(PostLike).where(PostLike.user_id == liker.id)
    )
    assert len(result.scalars().all()) == 2


# ── PostComment model ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_comment_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    commenter = make_user(email="commenter@example.com")
    db_session.add(commenter)
    await db_session.flush()

    comment = make_post_comment(post.id, commenter.id)
    db_session.add(comment)
    await db_session.flush()

    result = await db_session.execute(
        select(PostComment).where(PostComment.id == comment.id)
    )
    fetched = result.scalar_one()
    assert fetched.content == "Nice work!"
    assert fetched.parent_comment_id is None


@pytest.mark.asyncio
async def test_post_comment_reply_links_to_parent(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    commenter = make_user(email="commenter@example.com")
    db_session.add(commenter)
    await db_session.flush()

    top_level = make_post_comment(post.id, commenter.id, content="First!")
    db_session.add(top_level)
    await db_session.flush()

    reply = make_post_comment(
        post.id, commenter.id, content="Totally agree", parent_comment_id=top_level.id
    )
    db_session.add(reply)
    await db_session.flush()

    assert reply.parent_comment_id == top_level.id


# ── PostShare model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_share_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    sharer = make_user(email="sharer@example.com")
    db_session.add(sharer)
    await db_session.flush()

    share = make_post_share(post.id, sharer.id)
    db_session.add(share)
    await db_session.flush()

    result = await db_session.execute(select(PostShare).where(PostShare.id == share.id))
    fetched = result.scalar_one()
    assert fetched.post_id == post.id
    assert fetched.user_id == sharer.id


# ── PostSave model ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_save_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    saver = make_user(email="saver@example.com")
    db_session.add(saver)
    await db_session.flush()

    save = make_post_save(post.id, saver.id)
    db_session.add(save)
    await db_session.flush()

    result = await db_session.execute(select(PostSave).where(PostSave.id == save.id))
    fetched = result.scalar_one()
    assert fetched.post_id == post.id
    assert fetched.user_id == saver.id