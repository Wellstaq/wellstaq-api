"""
Category / HealthCategory model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, HealthCategory


# ── Factories ──────────────────────────────────────────────────────────────────

def make_category(name: str = "Wellness", **overrides) -> Category:
    defaults = dict(name=name, badge_color="#22C55E")
    return Category(**{**defaults, **overrides})


def make_health_category(name: str = "Nutrition", **overrides) -> HealthCategory:
    defaults = dict(name=name)
    return HealthCategory(**{**defaults, **overrides})


# ════════════════════════════════════════════════
# Category model
# ════════════════════════════════════════════════

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
async def test_category_badge_color_is_optional(db_session: AsyncSession):
    category = Category(name="Finance")
    db_session.add(category)
    await db_session.flush()

    assert category.badge_color is None


@pytest.mark.asyncio
async def test_multiple_categories_can_share_a_badge_color(db_session: AsyncSession):
    """No uniqueness constraint on badge_color — multiple categories can
    use the same color."""
    db_session.add(make_category(name="Wellness", badge_color="#22C55E"))
    db_session.add(make_category(name="Fitness", badge_color="#22C55E"))
    await db_session.flush()

    result = await db_session.execute(
        select(Category).where(Category.badge_color == "#22C55E")
    )
    assert len(result.scalars().all()) == 2


# ════════════════════════════════════════════════
# HealthCategory model
# ════════════════════════════════════════════════

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
async def test_multiple_health_categories_can_exist(db_session: AsyncSession):
    db_session.add(make_health_category(name="Nutrition"))
    db_session.add(make_health_category(name="Mental Health"))
    db_session.add(make_health_category(name="Sleep"))
    await db_session.flush()

    result = await db_session.execute(select(HealthCategory))
    assert len(result.scalars().all()) >= 3