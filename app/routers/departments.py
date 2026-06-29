"""
app/routers/departments.py

Department management routes.

Departments are org-level resources — creation authority always sits at the
org level. A department may be assigned to a branch post-creation via a
separate endpoint, but the CREATE action requires "department.create" held
org-wide (branch_id=NULL). A branch-scoped grant is explicitly insufficient.

Permission check sequence (CREATE / LIST / EDIT):
  1. require_permission("department.create") — flat decorator dependency;
     cheap early exit for callers who hold no grant at all.
  2. require_same_organization() — tenant isolation; sync, no DB.
  3. require_scoped_permission(..., target_branch_id=None) — authoritative
     org-level check; DB query confirming the grant is org-wide.
     super_admin bypasses automatically.
  4. Service call — business logic.

Permission check sequence (GET single department):
  1. require_same_organization() — tenant isolation; sync, no DB.
  2. Service resolves access: department.create grant OR membership.

Routes:
  POST   /organizations/{org_id}/departments               — create
  GET    /organizations/{org_id}/departments               — list all (paginated)
  GET    /organizations/{org_id}/departments/{dept_id}     — get one
  PATCH  /organizations/{org_id}/departments/{dept_id}     — edit
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_permission,
    require_scoped_permission,
    require_same_organization,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.department import (
    DepartmentCreateRequest,
    DepartmentListResponse,
    DepartmentResponse,
    DepartmentUpdateRequest,
)
from app.services.department import (
    create_department,
    edit_department,
    get_department,
    get_departments,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/organizations/{org_id}/departments",
    tags=["departments"],
)


# ── Create ─────────────────────────────────────────────────────────────────────

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
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments"):
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


# ── List ───────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=DepartmentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List departments in an organization",
    description=(
        "Returns all departments in the organization. "
        "Requires `department.create` granted org-wide."
    ),
)
async def list_departments_route(
    org_id: str,
    offset: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=50, ge=1, le=200, description="Max records to return"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.create")),
) -> DepartmentListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.create",
            target_branch_id=None,
        )

        departments = await get_departments(
            session=db,
            organization_id=org_id,
            offset=offset,
            limit=limit,
        )
        items = [DepartmentResponse.model_validate(d) for d in departments]
        return DepartmentListResponse(
            items=items,
            total=len(items),   # reflects current page; swap for COUNT query if needed
            offset=offset,
            limit=limit,
        )


# ── Get single ─────────────────────────────────────────────────────────────────

@router.get(
    "/{dept_id}",
    response_model=DepartmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a department by ID",
    description=(
        "Returns a single department. "
        "Accessible if the caller holds `department.create` org-wide, "
        "or if the caller is a member of the department."
    ),
)
async def get_department_route(
    org_id: str,
    dept_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DepartmentResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}"):
        # Resolve whether caller holds the broad permission; service uses this
        # to skip the membership check for privileged users.
       
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            user_id=current_user.user_id,
           
        )
        return DepartmentResponse.model_validate(department)


# ── Edit ───────────────────────────────────────────────────────────────────────

@router.patch(
    "/{dept_id}",
    response_model=DepartmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a department",
    description=(
        "Partially updates a department. All fields are optional — "
        "only provided fields are changed. "
        "Requires `department.create` granted org-wide."
    ),
)
async def edit_department_route(
    org_id: str,
    dept_id: str,
    body: DepartmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.update")),
) -> DepartmentResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.create",
            target_branch_id=None,
        )

        department = await edit_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            name=body.name,
            description=body.description,
            avatar_initial=body.avatar_initial,
        )
        await db.commit()
        return DepartmentResponse.model_validate(department)