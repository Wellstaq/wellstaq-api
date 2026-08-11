"""
app/routers/branch_activities.py

Branch-scoped activity aggregate. Visibility follows the same branch
rule used elsewhere (challenges, posts): own branch, or super_admin.
No dedicated permission exists for cross-branch activity viewing —
if that's ever needed, add an activity.view_all permission and mirror
check_post_branch_access's pattern rather than inventing scope not
backed by a real grant.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.activity import ActivitySummaryResponse, ActivityTypeCount
from app.services.activity import get_activity_trend, get_branch_activity_summary

router = APIRouter(tags=["activities"])


@router.get(
    "/organizations/{org_id}/branches/{branch_id}/activities/summary",
    response_model=ActivitySummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def branch_activity_summary_route(
    org_id: str, branch_id: str,
    period: str | None = Query(default=None, pattern="^(week|month|three_months|six_months|nine_months|year|custom)$"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActivitySummaryResponse:
    require_same_organization(org_id, current_user)
    if current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's activity data.")

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/activities/summary"):
        total, by_type = await get_branch_activity_summary(session=db, organization_id=org_id, branch_id=branch_id)
        trend = await get_activity_trend(session=db, organization_id=org_id, branch_id=branch_id, period=period, start_date=start_date, end_date=end_date)
        return ActivitySummaryResponse(
            total_count=total, by_type=[ActivityTypeCount(activity_type=t, count=c) for t, c in by_type], trend=trend,
        )
