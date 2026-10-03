"""
HTTP route tests for app/routers/categories.py.

Both global lookup endpoints are exercised through real authentication and
database queries. Tests cover empty responses, ordering, response allow-lists,
and separation between ordinary and health categories.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, HealthCategory
from tests.conftest import authenticate_user, make_org_with_owner


async def _headers(db_session: AsyncSession) -> dict[str, str]:
    _, owner = await make_org_with_owner(db_session, with_trial=False)
    headers, _ = await authenticate_user(
        db_session,
        owner,
        device_fingerprint=f"category-router-{owner.id}",
    )
    return headers


# ── Authentication ───────────────────────────────────────────────────────────

class TestCategoryRouteAuthentication:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/categories", "/health-categories"])
    async def test_lookup_requires_authentication(self, client, path: str):
        response = await client.get(path)

        assert response.status_code in {401, 403}


# ── Category lookup ──────────────────────────────────────────────────────────

class TestListCategoriesRoute:

    @pytest.mark.asyncio
    async def test_returns_empty_items(self, client, db_session: AsyncSession):
        response = await client.get("/categories", headers=await _headers(db_session))

        assert response.status_code == 200
        assert response.json() == {"items": []}

    @pytest.mark.asyncio
    async def test_returns_sorted_allow_list(self, client, db_session: AsyncSession):
        db_session.add_all([
            Category(name="Wellness", badge_color="#00FF00"),
            Category(name="Community", badge_color=None),
        ])
        await db_session.flush()

        response = await client.get("/categories", headers=await _headers(db_session))

        assert response.status_code == 200
        items = response.json()["items"]
        assert [row["name"] for row in items] == ["Community", "Wellness"]
        assert [row["badge_color"] for row in items] == [None, "#00FF00"]
        assert all(set(row) == {"id", "name", "badge_color"} for row in items)
        assert "created_at" not in response.text


# ── Health category lookup ───────────────────────────────────────────────────

class TestListHealthCategoriesRoute:

    @pytest.mark.asyncio
    async def test_returns_empty_items(self, client, db_session: AsyncSession):
        response = await client.get(
            "/health-categories", headers=await _headers(db_session)
        )

        assert response.status_code == 200
        assert response.json() == {"items": []}

    @pytest.mark.asyncio
    async def test_returns_only_health_categories_in_order(
        self, client, db_session: AsyncSession
    ):
        db_session.add(Category(name="Ordinary", badge_color="#000000"))
        db_session.add_all([
            HealthCategory(name="Sleep"),
            HealthCategory(name="Exercise"),
        ])
        await db_session.flush()

        response = await client.get(
            "/health-categories", headers=await _headers(db_session)
        )

        assert response.status_code == 200
        assert [row["name"] for row in response.json()["items"]] == [
            "Exercise",
            "Sleep",
        ]
        assert "Ordinary" not in response.text
        assert "badge_color" not in response.text
