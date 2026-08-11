"""
app/routers/activities.py

Access model:
  Raw activity feed (the actual list of what a user did) — self-only.
  No route exists to view another user's raw feed, by design — this is
  a privacy boundary, not an oversight.

  Aggregate summaries (org-wide, branch-wide, per-user count) — visible
  to any authenticated org member. These expose only counts, grouped by
  activity_type, never the underlying entries or timestamps of any
  individual action.

  There is no POST route — activities are logged internally by other
  services as a side effect (see log_activity), never created directly
  by a user hitting the API.

Routes:
  GET /organizations/{org_id}/activities/me                          — my own raw feed
  GET /organizations/{org_id}/activities/summary                      — org-wide aggregate
  GET /organizations/{org_id}/branches/{branch_id}/activities/summary — branch aggregate
  GET /organizations/{org_id}/activities/users/{user_id}/count        — one user's total count
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.activity import (
    ActivityItemResponse,
    ActivityListResponse,
    ActivitySummaryResponse,
    ActivityTypeCount,
    UserActivityCountResponse,
)
from app.services.activity import (
    get_activity_trend,
    get_branch_activity_summary,
    get_organization_activity_summary,
    get_user_activities,
    get_user_activity_count,
)

router = APIRouter(prefix="/organizations/{org_id}/activities", tags=["activities"])


@router.get("/me", response_model=ActivityListResponse, status_code=status.HTTP_200_OK)
async def list_my_activities_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActivityListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/activities/me"):
        activities, total = await get_user_activities(
            session=db, organization_id=org_id, user_id=current_user.user_id, offset=offset, limit=limit,
        )
        return ActivityListResponse(
            items=[ActivityItemResponse.model_validate(a) for a in activities], total=total, offset=offset, limit=limit,
        )


@router.get("/summary", response_model=ActivitySummaryResponse, status_code=status.HTTP_200_OK)
async def organization_activity_summary_route(
    org_id: str,
    period: str | None = Query(default=None, pattern="^(week|month|three_months|six_months|nine_months|year|custom)$"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActivitySummaryResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/activities/summary"):
        total, by_type = await get_organization_activity_summary(session=db, organization_id=org_id)
        trend = await get_activity_trend(session=db, organization_id=org_id, period=period, start_date=start_date, end_date=end_date)
        return ActivitySummaryResponse(
            total_count=total, by_type=[ActivityTypeCount(activity_type=t, count=c) for t, c in by_type], trend=trend,
        )


@router.get("/users/{user_id}/count", response_model=UserActivityCountResponse, status_code=status.HTTP_200_OK)
async def user_activity_count_route(
    org_id: str, user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> UserActivityCountResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/activities/users/{user_id}/count"):
        count = await get_user_activity_count(session=db, organization_id=org_id, user_id=user_id)
        return UserActivityCountResponse(user_id=user_id, total_count=count)
