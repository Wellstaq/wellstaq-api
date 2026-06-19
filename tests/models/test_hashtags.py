"""
Hashtag model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hashtag import Hashtag


# ── Factories ──────────────────────────────────────────────────────────────────

def make_hashtag(tag_name: str = "fitness", **overrides) -> Hashtag:
    defaults = dict(tag_name=tag_name, total_post_count=0)
    return Hashtag(**{**defaults, **overrides})


# ── Hashtag model ──────────────────────────────────────────────────────────────

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