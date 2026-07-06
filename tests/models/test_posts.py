"""
Post / PostHashtag / PostLike / PostComment / PostShare / PostSave model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Key fix vs prior version:
  User.branch_id is NOT NULL — setup_user() now requires branch_id and
  every call site passes owner.branch_id (the HQ branch auto-created by
  make_org_with_owner). make_club() also accepts branch_id for the same
  reason if Club.branch_id is required in your schema.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Club,
    Hashtag,
    Post,
    PostComment,
    PostHashtag,
    PostLike,
    PostSave,
    PostShare,
)
from tests.conftest import make_user, make_org_with_owner, make_user_with_role


# ── Factories ──────────────────────────────────────────────────────────────────

def make_club(organization_id: str, branch_id: str, **overrides) -> Club:
    defaults = dict(name="Running Club", organization_id=organization_id, branch_id=branch_id)
    return Club(**{**defaults, **overrides})


def make_post(user_id: str, organization_id: str, **overrides) -> Post:
    defaults = dict(
        user_id=user_id,
        organization_id=organization_id,
        content="Just hit a new PR!",
    )
    return Post(**{**defaults, **overrides})


def make_hashtag(organization_id: str, tag_name: str = "fitness", **overrides) -> Hashtag:
    defaults = dict(
        organization_id=organization_id,
        tag_name=tag_name,
        total_post_count=0,
    )
    return Hashtag(**{**defaults, **overrides})


def make_post_hashtag(organization_id: str, post_id: str, hashtag_id: str, **overrides) -> PostHashtag:
    return PostHashtag(
        organization_id=organization_id,
        post_id=post_id,
        hashtag_id=hashtag_id,
        **overrides,
    )


def make_post_like(organization_id: str, post_id: str, user_id: str, **overrides) -> PostLike:
    return PostLike(
        organization_id=organization_id,
        post_id=post_id,
        user_id=user_id,
        **overrides,
    )


def make_post_comment(organization_id: str, post_id: str, user_id: str, **overrides) -> PostComment:
    defaults = dict(content="Nice work!")
    return PostComment(
        organization_id=organization_id,
        post_id=post_id,
        user_id=user_id,
        **{**defaults, **overrides},
    )


def make_post_share(organization_id: str, post_id: str, user_id: str, **overrides) -> PostShare:
    return PostShare(
        organization_id=organization_id,
        post_id=post_id,
        user_id=user_id,
        **overrides,
    )


def make_post_save(organization_id: str, post_id: str, user_id: str, **overrides) -> PostSave:
    return PostSave(
        organization_id=organization_id,
        post_id=post_id,
        user_id=user_id,
        **overrides,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
):
    org, owner = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=slug),
        user_kwargs=dict(email=email, role_name="overall_manager"),
    )
    return owner, org


async def setup_post(
    db_session: AsyncSession,
    user_id: str,
    org_id: str,
    **overrides,
) -> Post:
    post = make_post(user_id, org_id, **overrides)
    db_session.add(post)
    await db_session.flush()
    return post


async def setup_user(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    **overrides,
):
    """
    branch_id is required — User.branch_id is NOT NULL.
    Pass owner.branch_id (the HQ branch) at every call site unless the
    test is specifically about branch assignment.
    """
    overrides.setdefault("email", "member@example.com")
    role_name = overrides.pop("role_name", "employee")
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        role_name=role_name,
        branch_id=branch_id,
        **overrides,
    )
    db_session.add(user)
    await db_session.flush()
    return user


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
    club = make_club(org.id, owner.branch_id)
    db_session.add(club)
    await db_session.flush()

    post = await setup_post(db_session, owner.id, org.id, club_id=club.id)

    assert post.club_id == club.id


# ── PostHashtag model ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_hashtag_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    hashtag = make_hashtag(org.id)
    db_session.add(hashtag)
    await db_session.flush()

    post_hashtag = make_post_hashtag(org.id, post.id, hashtag.id)
    db_session.add(post_hashtag)
    await db_session.flush()

    result = await db_session.execute(
        select(PostHashtag).where(PostHashtag.id == post_hashtag.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.post_id == post.id
    assert fetched.hashtag_id == hashtag.id


@pytest.mark.asyncio
async def test_post_can_have_multiple_hashtags(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    tag_a = make_hashtag(org.id, tag_name="running")
    tag_b = make_hashtag(org.id, tag_name="motivation")
    db_session.add_all([tag_a, tag_b])
    await db_session.flush()

    db_session.add(make_post_hashtag(org.id, post.id, tag_a.id))
    db_session.add(make_post_hashtag(org.id, post.id, tag_b.id))
    await db_session.flush()

    result = await db_session.execute(
        select(PostHashtag).where(PostHashtag.post_id == post.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_post_hashtag_organization_id_must_be_set(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)

    hashtag = make_hashtag(org.id, tag_name="required-org")
    db_session.add(hashtag)
    await db_session.flush()

    post_hashtag = PostHashtag(post_id=post.id, hashtag_id=hashtag.id)
    db_session.add(post_hashtag)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── PostLike model ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_like_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    liker = await setup_user(db_session, org.id, owner.branch_id, email="liker@example.com")

    like = make_post_like(org.id, post.id, liker.id)
    db_session.add(like)
    await db_session.flush()

    result = await db_session.execute(select(PostLike).where(PostLike.id == like.id))
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.post_id == post.id
    assert fetched.user_id == liker.id


@pytest.mark.asyncio
async def test_post_like_duplicate_raises(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    liker = await setup_user(db_session, org.id, owner.branch_id, email="liker@example.com")

    db_session.add(make_post_like(org.id, post.id, liker.id))
    await db_session.flush()

    db_session.add(make_post_like(org.id, post.id, liker.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_post_like_same_user_different_posts(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post_a = await setup_post(db_session, owner.id, org.id, content="Post A")
    post_b = await setup_post(db_session, owner.id, org.id, content="Post B")
    liker = await setup_user(db_session, org.id, owner.branch_id, email="liker@example.com")

    db_session.add(make_post_like(org.id, post_a.id, liker.id))
    db_session.add(make_post_like(org.id, post_b.id, liker.id))
    await db_session.flush()

    result = await db_session.execute(
        select(PostLike).where(PostLike.user_id == liker.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_post_like_unlike_then_relike(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    liker = await setup_user(db_session, org.id, owner.branch_id, email="liker@example.com")

    like = make_post_like(org.id, post.id, liker.id)
    db_session.add(like)
    await db_session.flush()

    await db_session.delete(like)
    await db_session.flush()

    db_session.add(make_post_like(org.id, post.id, liker.id))
    await db_session.flush()  # must NOT raise

    result = await db_session.execute(
        select(PostLike).where(PostLike.post_id == post.id, PostLike.user_id == liker.id)
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_post_like_organization_id_must_be_set(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    liker = await setup_user(
        db_session, org.id, owner.branch_id, email="liker_required@example.com"
    )

    like = PostLike(post_id=post.id, user_id=liker.id)
    db_session.add(like)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── PostComment model ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_comment_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    commenter = await setup_user(
        db_session, org.id, owner.branch_id, email="commenter@example.com"
    )

    comment = make_post_comment(org.id, post.id, commenter.id)
    db_session.add(comment)
    await db_session.flush()

    result = await db_session.execute(
        select(PostComment).where(PostComment.id == comment.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.content == "Nice work!"
    assert fetched.parent_comment_id is None


@pytest.mark.asyncio
async def test_post_comment_reply_links_to_parent(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    commenter = await setup_user(
        db_session, org.id, owner.branch_id, email="commenter@example.com"
    )

    top_level = make_post_comment(org.id, post.id, commenter.id, content="First!")
    db_session.add(top_level)
    await db_session.flush()

    reply = make_post_comment(
        org.id, post.id, commenter.id,
        content="Totally agree",
        parent_comment_id=top_level.id,
    )
    db_session.add(reply)
    await db_session.flush()

    assert reply.parent_comment_id == top_level.id


@pytest.mark.asyncio
async def test_post_comment_organization_id_must_be_set(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    commenter = await setup_user(
        db_session, org.id, owner.branch_id, email="commenter_required@example.com"
    )

    comment = PostComment(post_id=post.id, user_id=commenter.id, content="Missing org")
    db_session.add(comment)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── PostShare model ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_share_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    sharer = await setup_user(db_session, org.id, owner.branch_id, email="sharer@example.com")

    share = make_post_share(org.id, post.id, sharer.id)
    db_session.add(share)
    await db_session.flush()

    result = await db_session.execute(select(PostShare).where(PostShare.id == share.id))
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.post_id == post.id
    assert fetched.user_id == sharer.id


@pytest.mark.asyncio
async def test_post_share_same_user_can_share_repeatedly(db_session: AsyncSession):
    """Sharing is a repeatable event — unlike PostLike/PostSave, no
    unique constraint on (post_id, user_id)."""
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    sharer = await setup_user(db_session, org.id, owner.branch_id, email="sharer@example.com")

    db_session.add(make_post_share(org.id, post.id, sharer.id))
    db_session.add(make_post_share(org.id, post.id, sharer.id))
    await db_session.flush()  # must NOT raise

    result = await db_session.execute(
        select(PostShare).where(PostShare.post_id == post.id, PostShare.user_id == sharer.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_post_share_same_user_different_posts(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post_a = await setup_post(db_session, owner.id, org.id, content="Post A")
    post_b = await setup_post(db_session, owner.id, org.id, content="Post B")
    sharer = await setup_user(db_session, org.id, owner.branch_id, email="sharer@example.com")

    db_session.add(make_post_share(org.id, post_a.id, sharer.id))
    db_session.add(make_post_share(org.id, post_b.id, sharer.id))
    await db_session.flush()

    result = await db_session.execute(
        select(PostShare).where(PostShare.user_id == sharer.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_post_share_organization_id_must_be_set(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    sharer = await setup_user(
        db_session, org.id, owner.branch_id, email="sharer_required@example.com"
    )

    share = PostShare(post_id=post.id, user_id=sharer.id)
    db_session.add(share)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── PostSave model ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_save_persists(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    saver = await setup_user(db_session, org.id, owner.branch_id, email="saver@example.com")

    save = make_post_save(org.id, post.id, saver.id)
    db_session.add(save)
    await db_session.flush()

    result = await db_session.execute(select(PostSave).where(PostSave.id == save.id))
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.post_id == post.id
    assert fetched.user_id == saver.id


@pytest.mark.asyncio
async def test_post_save_duplicate_raises(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    saver = await setup_user(db_session, org.id, owner.branch_id, email="saver@example.com")

    db_session.add(make_post_save(org.id, post.id, saver.id))
    await db_session.flush()

    db_session.add(make_post_save(org.id, post.id, saver.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_post_save_same_user_different_posts(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post_a = await setup_post(db_session, owner.id, org.id, content="Post A")
    post_b = await setup_post(db_session, owner.id, org.id, content="Post B")
    saver = await setup_user(db_session, org.id, owner.branch_id, email="saver@example.com")

    db_session.add(make_post_save(org.id, post_a.id, saver.id))
    db_session.add(make_post_save(org.id, post_b.id, saver.id))
    await db_session.flush()

    result = await db_session.execute(
        select(PostSave).where(PostSave.user_id == saver.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_post_save_unsave_then_resave(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    saver = await setup_user(db_session, org.id, owner.branch_id, email="saver@example.com")

    save = make_post_save(org.id, post.id, saver.id)
    db_session.add(save)
    await db_session.flush()

    await db_session.delete(save)
    await db_session.flush()

    db_session.add(make_post_save(org.id, post.id, saver.id))
    await db_session.flush()  # must NOT raise

    result = await db_session.execute(
        select(PostSave).where(PostSave.post_id == post.id, PostSave.user_id == saver.id)
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_post_save_organization_id_must_be_set(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    post = await setup_post(db_session, owner.id, org.id)
    saver = await setup_user(
        db_session, org.id, owner.branch_id, email="saver_required@example.com"
    )

    save = PostSave(post_id=post.id, user_id=saver.id)
    db_session.add(save)
    with pytest.raises(IntegrityError):
        await db_session.flush()