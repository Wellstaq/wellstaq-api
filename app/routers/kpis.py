"""
app/routers/kpis.py

Read-only. KPISnapshot rows won't exist until compute_kpi_snapshots is
actually implemented — see app/jobs/analytics/kpi.py's open design
question. Route returns an empty list rather than 404 in that case,
since "no KPI data computed yet" is a normal, expected state, not an
error.

Routes:
  GET /organizations/{org_id}/branches/{branch_id}/kpi
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.kpi import KPIListResponse, KPISnapshotResponse
from app.services.kpi import get_branch_kpis

router = APIRouter(tags=["kpi"])


@router.get(
    "/organizations/{org_id}/branches/{branch_id}/kpi",
    response_model=KPIListResponse,
    status_code=status.HTTP_200_OK,
)
async def branch_kpi_route(
    org_id: str, branch_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> KPIListResponse:
    require_same_organization(org_id, current_user)
    if current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's KPI data.")

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/kpi"):
        snapshots = await get_branch_kpis(session=db, organization_id=org_id, branch_id=branch_id, limit=limit)
        return KPIListResponse(items=[KPISnapshotResponse.model_validate(s) for s in snapshots])