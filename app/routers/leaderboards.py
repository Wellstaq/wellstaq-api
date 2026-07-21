"""
app/routers/leaderboards.py

Read-only. Visibility mirrors the challenge/post branch-access pattern:
own branch always visible; any branch requires super_admin (no
leaderboard.view_all permission exists yet — add one and mirror
check_post_branch_access's approach if cross-branch viewing is needed).

Routes:
  GET /organizations/{org_id}/leaderboard/branch    — my branch, ranked
  GET /organizations/{org_id}/leaderboard/org        — org-wide, ranked
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.leaderboard import LeaderboardEntryResponse, LeaderboardResponse
from app.services.leaderboard import get_branch_leaderboard, get_org_leaderboard

router = APIRouter(prefix="/organizations/{org_id}/leaderboard", tags=["leaderboard"])


@router.get("/branch", response_model=LeaderboardResponse, status_code=status.HTTP_200_OK)
async def branch_leaderboard_route(
    org_id: str,
    metric_type: str = Query(...),
    period_type: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    branch_id: str | None = Query(default=None, description="Defaults to caller's own branch."),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LeaderboardResponse:
    require_same_organization(org_id, current_user)
    target_branch_id = branch_id or current_user.branch_id
    if branch_id is not None and current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's leaderboard.")

    async with request_guard(db, "/organizations/{org_id}/leaderboard/branch"):
        rows, period_start, period_end = await get_branch_leaderboard(
            session=db, organization_id=org_id, branch_id=target_branch_id,
            metric_type=metric_type.strip().lower(), period_type=period_type, limit=limit,
        )
        if period_start is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No leaderboard data available yet for this metric/period.")

        items = [
            LeaderboardEntryResponse(
                user_id=u.id, first_name=u.first_name, last_name=u.last_name, value=s.value,
                rank=s.rank, previous_rank=s.previous_rank, org_rank=s.org_rank, previous_org_rank=s.previous_org_rank,
            )
            for s, u in rows
        ]
        return LeaderboardResponse(
            metric_type=metric_type.strip().lower(), period_type=period_type,
            period_start=period_start, period_end=period_end, scope="branch", items=items,
        )


@router.get("/org", response_model=LeaderboardResponse, status_code=status.HTTP_200_OK)
async def org_leaderboard_route(
    org_id: str,
    metric_type: str = Query(...),
    period_type: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LeaderboardResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/leaderboard/org"):
        rows, period_start, period_end = await get_org_leaderboard(
            session=db, organization_id=org_id, metric_type=metric_type.strip().lower(),
            period_type=period_type, limit=limit,
        )
        
        if period_start is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No leaderboard data available yet for this metric/period.")

        items = [
            LeaderboardEntryResponse(
                user_id=u.id, first_name=u.first_name, last_name=u.last_name, value=s.value,
                rank=s.rank, previous_rank=s.previous_rank, org_rank=s.org_rank, previous_org_rank=s.previous_org_rank,
            )
            for s, u in rows
        ]
        return LeaderboardResponse(
            metric_type=metric_type.strip().lower(), period_type=period_type,
            period_start=period_start, period_end=period_end, scope="org", items=items,
        )