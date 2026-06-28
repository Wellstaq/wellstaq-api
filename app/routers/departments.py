"""
app/routers/departments.py

Department management routes.

Departments are org-level resources — creation authority always sits at the
org level. A department may be assigned to a branch post-creation via a
separate endpoint, but the CREATE action requires "department.create" held
org-wide (branch_id=NULL). A branch-scoped grant is explicitly insufficient.

Permission check sequence:
  1. require_permission("department.create") — flat decorator dependency;
     cheap early exit for callers who hold no grant at all.
  2. require_same_organization() — tenant isolation; sync, no DB.
  3. require_scoped_permission(..., target_branch_id=None) — authoritative
     org-level check; DB query confirming the grant is org-wide.
     super_admin bypasses automatically.
  4. create_department() — business logic (org active, name unique, insert).

Routes:
  POST /organizations/{org_id}/departments — create a department
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    require_permission,
    require_scoped_permission,
    require_same_organization,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.department import DepartmentCreateRequest, DepartmentResponse
from app.services.department import create_department

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/organizations/{org_id}/departments",
    tags=["departments"],
)


@router.post(
    "",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a department",
    description=(
        "Creates a new department at the organization level. "
        "Requires `department.create` granted org-wide. "
        "A branch-scoped grant is not sufficient."
    ),
)
async def create_department_route(
    org_id: str,
    body: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.create")),
) -> DepartmentResponse:
    # Sync tenant isolation check — no DB hit
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments"):
        # Authoritative org-level scope check (DB query)
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.create",
            target_branch_id=None,
        )

        department = await create_department(
            session=db,
            organization_id=org_id,
            name=body.name,
            description=body.description,
            avatar_initial=body.avatar_initial,
            created_by=current_user.user_id,
        )
        await db.commit()
        return DepartmentResponse.model_validate(department)