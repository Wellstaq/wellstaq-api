"""
app/routers/categories.py

Read-only global lookups, no org scoping — same category as Category's
own model design (shared across every organization). No mutation routes:
these are seed data (see scripts/seed_categories.py), never created via
the API.

health-categories is currently dormant (health-condition tracking was
stripped from the UI per the owner's decision) but the route stays —
harmless to serve, and ready for when that feature returns.

Routes:
  GET /categories
  GET /health-categories
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.category import CategoryListResponse, CategoryResponse, HealthCategoryListResponse, HealthCategoryResponse
from app.services.category import list_categories, list_health_categories

router = APIRouter(tags=["categories"])


@router.get("/categories", response_model=CategoryListResponse, status_code=status.HTTP_200_OK)
async def list_categories_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> CategoryListResponse:
    async with request_guard(db, "/categories"):
        categories = await list_categories(session=db)
        return CategoryListResponse(items=[CategoryResponse.model_validate(c) for c in categories])


@router.get("/health-categories", response_model=HealthCategoryListResponse, status_code=status.HTTP_200_OK)
async def list_health_categories_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> HealthCategoryListResponse:
    async with request_guard(db, "/health-categories"):
        health_categories = await list_health_categories(session=db)
        return HealthCategoryListResponse(items=[HealthCategoryResponse.model_validate(h) for h in health_categories])