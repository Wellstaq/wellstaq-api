"""
app/routers/engagement_log.py

Self-only, read-only. EngagementLog rows are written exclusively by
compute_engagement_logs (see app/jobs/tasks.py) — there is no submit
route, same as every other job-computed table (LeaderboardSnapshot,
KPISnapshot, DepartmentRankSnapshot).

Routes:
  GET /engagement-log/trend    — my trend for one metric_type
  GET /engagement-log/snapshot — my latest value across all 4 metric_types
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.engagement_log import EngagementSnapshotResponse, EngagementTrendPoint, EngagementTrendResponse
from app.services.engagement_log import ENGAGEMENT_METRIC_TYPES, get_engagement_trend, get_latest_snapshot

router = APIRouter(prefix="/engagement-log", tags=["engagement-log"])


@router.get("/trend", response_model=EngagementTrendResponse, status_code=status.HTTP_200_OK)
async def get_engagement_trend_route(
    metric_type: str = Query(...),
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EngagementTrendResponse:
    normalized = metric_type.strip().lower()
    if normalized not in ENGAGEMENT_METRIC_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"metric_type must be one of {ENGAGEMENT_METRIC_TYPES}.")

    async with request_guard(db, "/engagement-log/trend"):
        points = await get_engagement_trend(session=db, user_id=current_user.user_id, metric_type=normalized, limit=limit)
        return EngagementTrendResponse(
            metric_type=normalized,
            points=[EngagementTrendPoint(date=d, value=v, previous_value=p, trend_direction=t) for d, v, p, t in points],
        )


@router.get("/snapshot", response_model=EngagementSnapshotResponse, status_code=status.HTTP_200_OK)
async def get_engagement_snapshot_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EngagementSnapshotResponse:
    async with request_guard(db, "/engagement-log/snapshot"):
        snapshot = await get_latest_snapshot(session=db, user_id=current_user.user_id)
        return EngagementSnapshotResponse(**snapshot)
