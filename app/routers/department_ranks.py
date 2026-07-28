"""
app/routers/department_rank.py

Read-only. Org-wide view is open to any org member (it's an aggregate
comparison across departments, no individual-level data exposed — same
reasoning as the branch activity summary). Per-department history is
also open — a department's performance-over-time isn't individually
sensitive the way personal health data is.

Routes:
  GET /organizations/{org_id}/department-rank                          — latest ranking, all departments
  GET /organizations/{org_id}/departments/{dept_id}/rank-history        — one department's history over time
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization, require_active_subscription
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.department_rank import DepartmentRankListResponse, DepartmentRankResponse
from app.services.department_rank import get_department_rank_history, get_organization_department_ranks

router = APIRouter(tags=["department-rank"])


@router.get(
    "/organizations/{org_id}/department-rank",
    response_model=DepartmentRankListResponse,
    status_code=status.HTTP_200_OK,
)
async def organization_department_ranks_route(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DepartmentRankListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/department-rank"):
        rows = await get_organization_department_ranks(session=db, organization_id=org_id)
        items = [
            DepartmentRankResponse(
                department_id=dept.id, department_name=dept.name, rank=snap.rank,
                total_activities=snap.total_activities, avg_daily_steps=snap.avg_daily_steps,
                performance_score=snap.performance_score, period_start=snap.period_start, period_end=snap.period_end,
            )
            for snap, dept in rows
        ]
        return DepartmentRankListResponse(items=items)


@router.get(
    "/organizations/{org_id}/departments/{dept_id}/rank-history",
    response_model=DepartmentRankListResponse,
    status_code=status.HTTP_200_OK,
)
async def department_rank_history_route(
    org_id: str, dept_id: str,
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DepartmentRankListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}/rank-history"):
        from app.models import Department
        from sqlalchemy import select as _select
        dept_result = await db.execute(_select(Department).where(Department.id == dept_id, Department.organization_id == org_id))
        department = dept_result.scalar_one_or_none()
        if department is None:
            from fastapi import HTTPException
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found.")

        snapshots = await get_department_rank_history(session=db, department_id=dept_id, limit=limit)
        items = [
            DepartmentRankResponse(
                department_id=department.id, department_name=department.name, rank=s.rank,
                total_activities=s.total_activities, avg_daily_steps=s.avg_daily_steps,
                performance_score=s.performance_score, period_start=s.period_start, period_end=s.period_end,
            )
            for s in snapshots
        ]
        return DepartmentRankListResponse(items=items)