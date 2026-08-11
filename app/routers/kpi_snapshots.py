"""
app/routers/kpi_snapshots.py

Read-only — rows are written exclusively by the backend scheduler
(compute_kpi_snapshots job). No POST/PUT/DELETE here.

Permission model:
  Same reasoning as targets.py — no dedicated KPI-snapshot permission
  exists; view access follows the same "own branch, or super_admin /
  branch.update holder for cross-branch" pattern as target progress
  and activity trend routes. Reading a computed KPI isn't individually
  sensitive.

Routes:
  GET /organizations/{org_id}/branches/{branch_id}/kpi-snapshots
  GET /organizations/{org_id}/kpi-snapshots/org-wide
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization, require_scoped_permission
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.kpi_snapshot import KPIOverviewResponse, KPISnapshotListResponse, KPISnapshotResponse, OrgWideKPIPoint, OrgWideKPIResponse
from app.services.kpi_snapshot import get_kpi_overview, get_org_wide_kpi_snapshots, list_branch_kpi_snapshots

router = APIRouter(tags=["kpi-snapshots"])


@router.get(
    "/organizations/{org_id}/kpi-snapshots/overview",
    response_model=KPIOverviewResponse,
    status_code=status.HTTP_200_OK,
)
async def kpi_overview_route(
    org_id: str,
    period: str = Query(default="nine_months", pattern="^(month|three_months|six_months|nine_months|year)$"),
    branch_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> KPIOverviewResponse:
    require_same_organization(org_id, current_user)
    await require_scoped_permission(
        db=db, current_user=current_user, permission="wellbeing.view_team", target_branch_id=branch_id,
    )
    async with request_guard(db, "/organizations/{org_id}/kpi-snapshots/overview"):
        return KPIOverviewResponse(**await get_kpi_overview(
            session=db, organization_id=org_id, period=period, branch_id=branch_id,
        ))


@router.get(
    "/organizations/{org_id}/branches/{branch_id}/kpi-snapshots",
    response_model=KPISnapshotListResponse, status_code=status.HTTP_200_OK,
)
async def list_branch_kpi_snapshots_route(
    org_id: str, branch_id: str,
    metric_name: str = Query(...),
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> KPISnapshotListResponse:
    require_same_organization(org_id, current_user)
    if current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's KPI data.")

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/kpi-snapshots"):
        snapshots = await list_branch_kpi_snapshots(
            session=db, organization_id=org_id, branch_id=branch_id,
            metric_name=metric_name, period_start=period_start, period_end=period_end,
        )
        return KPISnapshotListResponse(items=[KPISnapshotResponse.model_validate(s) for s in snapshots])


@router.get(
    "/organizations/{org_id}/kpi-snapshots/org-wide",
    response_model=OrgWideKPIResponse, status_code=status.HTTP_200_OK,
)
async def get_org_wide_kpi_snapshots_route(
    org_id: str,
    metric_name: str = Query(...),
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> OrgWideKPIResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/kpi-snapshots/org-wide"):
        points = await get_org_wide_kpi_snapshots(
            session=db, organization_id=org_id, metric_name=metric_name,
            period_start=period_start, period_end=period_end,
        )
        normalized_metric_name = metric_name.strip().lower()
        return OrgWideKPIResponse(
            organization_id=org_id, metric_name=normalized_metric_name,
            points=[OrgWideKPIPoint(**p) for p in points],
        )
