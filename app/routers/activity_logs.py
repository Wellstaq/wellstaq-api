"""
app/routers/activity_logs.py

Self-only ingestion and personal trend viewing. See submit_activity's
docstring for the full fan-out behavior (feeds ActivityLog +
ChallengeProgressEntry from one submission).

Routes:
  POST /organizations/{org_id}/activity-log            — submit today's metric
  GET  /organizations/{org_id}/activity-log/trend       — my trend for one metric_type
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.activity_log import (
    ActivitySubmitRequest,
    ActivitySubmitResponse,
    ActivityTrendPoint,
    ActivityTrendResponse,
)
from app.schemas.activity_log import ActivityLogResponse
from app.services.activity_log import get_activity_trend, submit_activity

router = APIRouter(prefix="/organizations/{org_id}/activity-log", tags=["activity-log"])


@router.post(
    "",
    response_model=ActivitySubmitResponse,
    status_code=status.HTTP_201_CREATED,
    description=(
        "Submits today's value for one metric_type (e.g. steps). Upserts "
        "today's ActivityLog entry AND automatically feeds progress into "
        "any active challenge the caller is participating in with a "
        "matching metric_type — no separate challenge submission needed."
    ),
)
async def submit_activity_route(
    org_id: str, body: ActivitySubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActivitySubmitResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/activity-log"):
        entry, challenges_updated = await submit_activity(
            session=db, organization_id=org_id, branch_id=current_user.branch_id, user_id=current_user.user_id,
            metric_type=body.metric_type, value=body.value, unit=body.unit, source=body.source,
        )
        await db.commit()
        return ActivitySubmitResponse(entry=ActivityLogResponse.model_validate(entry), challenges_updated=challenges_updated)


@router.get("/trend", response_model=ActivityTrendResponse, status_code=status.HTTP_200_OK)
async def get_activity_trend_route(
    org_id: str,
    metric_type: str = Query(...),
    granularity: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActivityTrendResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/activity-log/trend"):
        points = await get_activity_trend(
            session=db, user_id=current_user.user_id, metric_type=metric_type, granularity=granularity, limit=limit,
        )
        return ActivityTrendResponse(
            metric_type=metric_type.strip().lower(), granularity=granularity,
            points=[ActivityTrendPoint(period=p, value=v) for p, v in points],
        )