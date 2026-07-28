"""
app/routers/targets.py

Permission model:
  Set/delete — branch.update-equivalent... actually no dedicated
  permission exists for targets specifically. Using department.update
  would be wrong (unrelated domain). Reusing branch.update, since a
  target is fundamentally a branch-level configuration, same category
  as other branch settings.

  View progress — open to any member of that branch (or super_admin /
  branch.update holder for cross-branch view) — same pattern as
  activity trend/stats routes; a target and current progress toward it
  isn't individually sensitive data.

Routes:
  PUT    /organizations/{org_id}/branches/{branch_id}/targets
  GET    /organizations/{org_id}/branches/{branch_id}/targets
  DELETE /organizations/{org_id}/targets/{target_id}
  GET    /organizations/{org_id}/branches/{branch_id}/targets/progress
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_permission, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.target import TargetListResponse, TargetProgressResponse, TargetResponse, TargetSetRequest
from app.services.target import delete_target, get_target_progress, list_branch_targets, set_target

router = APIRouter(tags=["targets"])


@router.put(
    "/organizations/{org_id}/branches/{branch_id}/targets",
    response_model=TargetResponse, status_code=status.HTTP_200_OK,
)
async def set_target_route(
    org_id: str, branch_id: str, body: TargetSetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.update")),
) -> TargetResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/targets"):
        from app.auth.dependencies import require_scoped_permission
        await require_scoped_permission(db=db, current_user=current_user, permission="branch.update", target_branch_id=branch_id)

        target = await set_target(
            session=db, organization_id=org_id, branch_id=branch_id,
            metric_type=body.metric_type, period=body.period, target_value=body.target_value,
        )
        await db.commit()
        return TargetResponse.model_validate(target)


@router.get(
    "/organizations/{org_id}/branches/{branch_id}/targets",
    response_model=TargetListResponse, status_code=status.HTTP_200_OK,
)
async def list_targets_route(
    org_id: str, branch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> TargetListResponse:
    require_same_organization(org_id, current_user)
    if current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's targets.")

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/targets"):
        targets = await list_branch_targets(session=db, organization_id=org_id, branch_id=branch_id)
        return TargetListResponse(items=[TargetResponse.model_validate(t) for t in targets])


@router.delete("/organizations/{org_id}/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target_route(
    org_id: str, target_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.update")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/targets/{target_id}"):
        from app.services.target._helpers import get_target_or_404
        target = await get_target_or_404(session=db, organization_id=org_id, target_id=target_id)

        from app.auth.dependencies import require_scoped_permission
        await require_scoped_permission(db=db, current_user=current_user, permission="branch.update", target_branch_id=target.branch_id)

        await delete_target(session=db, organization_id=org_id, target_id=target_id)
        await db.commit()


@router.get(
    "/organizations/{org_id}/branches/{branch_id}/targets/progress",
    response_model=TargetProgressResponse, status_code=status.HTTP_200_OK,
)
async def target_progress_route(
    org_id: str, branch_id: str,
    metric_type: str = Query(...), period_type: str = Query(...), period: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> TargetProgressResponse:
    require_same_organization(org_id, current_user)
    if current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's target progress.")

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/targets/progress"):
        progress = await get_target_progress(
            session=db, organization_id=org_id, branch_id=branch_id,
            metric_type=metric_type, period_type=period_type, period=period,
        )
        return TargetProgressResponse(**progress)