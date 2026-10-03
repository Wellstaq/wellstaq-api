"""
Tests for app/services/category/get_category.py.

Category lookups are global seed data. These tests cover empty results,
alphabetical ordering, and preservation of the two distinct lookup types.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, HealthCategory
from app.services.category.get_category import list_categories, list_health_categories


class TestListCategories:

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_categories_exist(
        self, db_session: AsyncSession
    ):
        assert await list_categories(session=db_session) == []

    @pytest.mark.asyncio
    async def test_returns_categories_in_alphabetical_order(
        self, db_session: AsyncSession
    ):
        db_session.add_all([
            Category(name="Wellness", badge_color="#00FF00"),
            Category(name="Fitness", badge_color="#FF0000"),
            Category(name="Community", badge_color=None),
        ])
        await db_session.flush()

        rows = await list_categories(session=db_session)

        assert [row.name for row in rows] == ["Community", "Fitness", "Wellness"]
        assert rows[0].badge_color is None


class TestListHealthCategories:

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_health_categories_exist(
        self, db_session: AsyncSession
    ):
        assert await list_health_categories(session=db_session) == []

    @pytest.mark.asyncio
    async def test_returns_health_categories_in_alphabetical_order(
        self, db_session: AsyncSession
    ):
        db_session.add_all([
            HealthCategory(name="Sleep"),
            HealthCategory(name="Mental Health"),
            HealthCategory(name="Exercise"),
        ])
        await db_session.flush()

        rows = await list_health_categories(session=db_session)

        assert [row.name for row in rows] == ["Exercise", "Mental Health", "Sleep"]
