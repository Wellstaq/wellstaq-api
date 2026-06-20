"""
Hashtag / HashtagTrendSnapshot model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hashtag, HashtagTrendSnapshot, Organization, User


# ── Factories ──────────────────────────────────────────────────────────────────

def make_hashtag(tag_name: str = "fitness", **overrides) -> Hashtag:
    defaults = dict(tag_name=tag_name, total_post_count=0)
    return Hashtag(**{**defaults, **overrides})


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


def make_snapshot(hashtag_id: str, organization_id: str, **overrides) -> HashtagTrendSnapshot:
    now = datetime(2026, 1, 1, 12, 0, 0)
    defaults = dict(
        post_count=10,
        window_start=now - timedelta(minutes=15),
        window_end=now,
        trend_score=42.5,
        computed_at=now,
    )
    return HashtagTrendSnapshot(
        hashtag_id=hashtag_id,
        organization_id=organization_id,
        **{**defaults, **overrides},
    )


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


# ════════════════════════════════════════════════
# Hashtag model
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hashtag_persists(db_session: AsyncSession):
    hashtag = make_hashtag()
    db_session.add(hashtag)
    await db_session.flush()

    result = await db_session.execute(
        select(Hashtag).where(Hashtag.id == hashtag.id)
    )
    fetched = result.scalar_one()
    assert fetched.tag_name == "fitness"
    assert fetched.total_post_count == 0


@pytest.mark.asyncio
async def test_hashtag_id_is_uuid_format(db_session: AsyncSession):
    hashtag = make_hashtag()
    db_session.add(hashtag)
    await db_session.flush()

    parts = hashtag.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_hashtag_tag_name_must_be_unique(db_session: AsyncSession):
    db_session.add(make_hashtag(tag_name="wellness"))
    await db_session.flush()

    db_session.add(make_hashtag(tag_name="wellness"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_hashtag_total_post_count_defaults_to_zero(db_session: AsyncSession):
    hashtag = Hashtag(tag_name="mindfulness")
    db_session.add(hashtag)
    await db_session.flush()

    assert hashtag.total_post_count == 0


@pytest.mark.asyncio
async def test_hashtag_total_post_count_can_increment(db_session: AsyncSession):
    hashtag = make_hashtag(tag_name="running")
    db_session.add(hashtag)
    await db_session.flush()

    hashtag.total_post_count += 1
    await db_session.flush()

    result = await db_session.execute(
        select(Hashtag).where(Hashtag.id == hashtag.id)
    )
    fetched = result.scalar_one()
    assert fetched.total_post_count == 1


# ════════════════════════════════════════════════
# HashtagTrendSnapshot model
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    hashtag = make_hashtag(tag_name="snapshot-fitness")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id)
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()
    assert fetched.hashtag_id == hashtag.id
    assert fetched.organization_id == org.id
    assert fetched.post_count == 10
    assert float(fetched.trend_score) == 42.5


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    hashtag = make_hashtag(tag_name="snapshot-uuid")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id)
    db_session.add(snapshot)
    await db_session.flush()

    parts = snapshot.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_window_fields(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    hashtag = make_hashtag(tag_name="snapshot-window")
    db_session.add(hashtag)
    await db_session.flush()

    window_start = datetime(2026, 1, 1, 9, 0, 0)
    window_end = datetime(2026, 1, 1, 10, 0, 0)

    snapshot = make_snapshot(
        hashtag.id, org.id,
        window_start=window_start,
        window_end=window_end,
    )
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.window_start == window_start
    assert snapshot.window_end == window_end


@pytest.mark.asyncio
async def test_hashtag_can_have_multiple_trend_snapshots(db_session: AsyncSession):
    """Snapshots accumulate over time — one row per computation run, no
    uniqueness constraint."""
    _, org = await setup_org(db_session)
    hashtag = make_hashtag(tag_name="snapshot-multi")
    db_session.add(hashtag)
    await db_session.flush()

    db_session.add(
        make_snapshot(
            hashtag.id, org.id,
            window_start=datetime(2026, 1, 1, 9, 0, 0),
            window_end=datetime(2026, 1, 1, 9, 15, 0),
        )
    )
    db_session.add(
        make_snapshot(
            hashtag.id, org.id,
            window_start=datetime(2026, 1, 1, 9, 15, 0),
            window_end=datetime(2026, 1, 1, 9, 30, 0),
        )
    )
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.hashtag_id == hashtag.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_hashtag_trend_snapshot_score_is_numeric(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    hashtag = make_hashtag(tag_name="snapshot-numeric")
    db_session.add(hashtag)
    await db_session.flush()

    snapshot = make_snapshot(hashtag.id, org.id, trend_score=99.9999)
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()
    assert float(fetched.trend_score) == 99.9999


@pytest.mark.asyncio
async def test_same_hashtag_different_orgs_have_separate_snapshots(db_session: AsyncSession):
    """Trending is scoped per organization — the same hashtag can have
    different trend scores in different orgs."""
    owner_a, org_a = await setup_org(db_session, email="owner_a@example.com", slug="org-a")
    owner_b = make_user(email="owner_b@example.com")
    db_session.add(owner_b)
    await db_session.flush()
    org_b = make_org(owner_b.id, slug="org-b")
    db_session.add(org_b)
    await db_session.flush()

    hashtag = make_hashtag(tag_name="snapshot-multiorg")
    db_session.add(hashtag)
    await db_session.flush()

    db_session.add(make_snapshot(hashtag.id, org_a.id, trend_score=10))
    db_session.add(make_snapshot(hashtag.id, org_b.id, trend_score=90))
    await db_session.flush()

    result = await db_session.execute(
        select(HashtagTrendSnapshot).where(HashtagTrendSnapshot.hashtag_id == hashtag.id)
    )
    snapshots = result.scalars().all()
    assert len(snapshots) == 2
    scores = sorted(float(s.trend_score) for s in snapshots)
    assert scores == [10, 90]