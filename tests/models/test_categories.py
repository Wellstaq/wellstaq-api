"""
Category / HealthCategory model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, HealthCategory


# ── Local Factories ────────────────────────────────────────────────────────────

def make_category(name: str = "Wellness", **overrides) -> Category:
    defaults = dict(name=name, badge_color="#22C55E")
    return Category(**{**defaults, **overrides})


def make_health_category(name: str = "Nutrition", **overrides) -> HealthCategory:
    defaults = dict(name=name)
    return HealthCategory(**{**defaults, **overrides})


# ── Category Model Tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_category_persists(db_session: AsyncSession):
    category = make_category()
    db_session.add(category)
    await db_session.flush()

    result = await db_session.execute(select(Category).where(Category.id == category.id))
    fetched = result.scalar_one()
    assert fetched.name == "Wellness"
    assert fetched.badge_color == "#22C55E"


@pytest.mark.asyncio
async def test_category_id_is_uuid_format(db_session: AsyncSession):
    category = make_category()
    db_session.add(category)
    await db_session.flush()

    parts = category.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_category_timestamps_auto_set(db_session: AsyncSession):
    category = make_category()
    db_session.add(category)
    await db_session.flush()

    assert category.created_at is not None
    assert category.updated_at is not None


@pytest.mark.asyncio
async def test_category_badge_color_is_optional(db_session: AsyncSession):
    category = Category(name="Finance")
    db_session.add(category)
    await db_session.flush()

    assert category.badge_color is None


@pytest.mark.asyncio
async def test_multiple_categories_can_share_a_badge_color(db_session: AsyncSession):
    """No uniqueness constraint on badge_color — multiple categories can use the same color."""
    db_session.add(make_category(name="Wellness", badge_color="#22C55E"))
    db_session.add(make_category(name="Fitness", badge_color="#22C55E"))
    await db_session.flush()

    result = await db_session.execute(
        select(Category).where(Category.badge_color == "#22C55E")
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_category_name_is_strictly_unique(db_session: AsyncSession):
    """Controlled vocabulary lookup table must block duplicate category names."""
    db_session.add(make_category(name="Technology"))
    await db_session.flush()

    db_session.add(make_category(name="Technology"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_category_name_cannot_be_empty_or_whitespace(db_session: AsyncSession):
    """CheckConstraint must catch empty strings or spaces-only names."""
    # Empty string
    db_session.add(make_category(name=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    # Whitespace characters
    db_session.add(make_category(name="     "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_category_relationship_collections_initialize(db_session: AsyncSession):
    """Ensure relationship arrays map correctly and start empty."""
    category = make_category()
    db_session.add(category)
    await db_session.flush()

    # Explicitly refresh relationships asynchronously to avoid lazy-load errors
    await db_session.refresh(category, ["departments", "clubs"])

    assert isinstance(category.departments, list)
    assert isinstance(category.clubs, list)
    assert len(category.departments) == 0
    assert len(category.clubs) == 0


# ── HealthCategory Model Tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_category_persists(db_session: AsyncSession):
    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    result = await db_session.execute(
        select(HealthCategory).where(HealthCategory.id == category.id)
    )
    fetched = result.scalar_one()
    assert fetched.name == "Nutrition"


@pytest.mark.asyncio
async def test_health_category_id_is_uuid_format(db_session: AsyncSession):
    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    parts = category.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_health_category_timestamps_auto_set(db_session: AsyncSession):
    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    assert category.created_at is not None
    assert category.updated_at is not None


@pytest.mark.asyncio
async def test_multiple_health_categories_can_exist(db_session: AsyncSession):
    db_session.add(make_health_category(name="Nutrition"))
    db_session.add(make_health_category(name="Mental Health"))
    db_session.add(make_health_category(name="Sleep"))
    await db_session.flush()

    result = await db_session.execute(select(HealthCategory))
    assert len(result.scalars().all()) >= 3


@pytest.mark.asyncio
async def test_health_category_name_is_strictly_unique(db_session: AsyncSession):
    """Controlled vocabulary lookup table must block duplicate clinical category names."""
    db_session.add(make_health_category(name="Exercise"))
    await db_session.flush()

    db_session.add(make_health_category(name="Exercise"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_health_category_name_cannot_be_empty_or_whitespace(db_session: AsyncSession):
    """CheckConstraint must catch empty strings or spaces-only health names."""
    # Empty string
    db_session.add(make_health_category(name=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    # Whitespace characters
    db_session.add(make_health_category(name="  "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_health_category_relationship_collections_initialize(db_session: AsyncSession):
    """Ensure assessment backend relation definitions load correctly."""
    category = make_health_category()
    db_session.add(category)
    await db_session.flush()

    # Explicitly refresh relationship asynchronously to avoid lazy-load errors
    await db_session.refresh(category, ["assessments"])

    assert isinstance(category.assessments, list)
    assert len(category.assessments) == 0