"""
app/routers/dashboard.py

Single composed "everything the dashboard needs on load" endpoint.
See conversation history for the backend-vs-frontend-composition
tradeoff discussion — kept backend-composed specifically because it's
the natural first candidate for Redis caching (parking lot item).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_same_organization,
    require_scoped_permission,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.dashboard import DashboardBootstrapResponse, EngagementWellbeingTrendsResponse
from app.services.dashboard.bootstrap_service import get_dashboard_bootstrap
from app.services.dashboard.trend_service import get_engagement_wellbeing_trends

router = APIRouter(prefix="/organizations/{org_id}/dashboard", tags=["dashboard"])


@router.get("/bootstrap", response_model=DashboardBootstrapResponse, status_code=status.HTTP_200_OK)
async def dashboard_bootstrap_route(
    org_id: str,
    metric_type: str = Query(default="steps"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DashboardBootstrapResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/dashboard/bootstrap"):
        data = await get_dashboard_bootstrap(
            session=db, organization_id=org_id, user_id=current_user.user_id,
            role=current_user.role, branch_id=current_user.branch_id,
            permissions=current_user.permissions, metric_type=metric_type,
        )
        return DashboardBootstrapResponse(**data)


@router.get(
    "/engagement-wellbeing-trends",
    response_model=EngagementWellbeingTrendsResponse,
    status_code=status.HTTP_200_OK,
)
async def engagement_wellbeing_trends_route(
    org_id: str,
    period: Literal["week", "month", "three_months"] = Query(default="week"),
    branch_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EngagementWellbeingTrendsResponse:
    require_same_organization(org_id, current_user)
    await require_scoped_permission(
        db=db,
        current_user=current_user,
        permission="wellbeing.view_team",
        target_branch_id=branch_id,
    )
    async with request_guard(db, "/organizations/{org_id}/dashboard/engagement-wellbeing-trends"):
        data = await get_engagement_wellbeing_trends(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
            period=period,
        )
        return EngagementWellbeingTrendsResponse(**data)
