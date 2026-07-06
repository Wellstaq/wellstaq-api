"""
app/routers/departments.py

Department management routes.

Routes:
  POST   /organizations/{org_id}/departments                                — create
  GET    /organizations/{org_id}/departments                                — list all
  GET    /organizations/{org_id}/departments/{dept_id}                      — get one
  PATCH  /organizations/{org_id}/departments/{dept_id}                      — edit
  DELETE /organizations/{org_id}/departments/{dept_id}                      — delete
  PUT    /organizations/{org_id}/departments/{dept_id}/members/{user_id}    — assign user
  PUT    /organizations/{org_id}/departments/{dept_id}/head/{user_id}       — assign head
  DELETE /organizations/{org_id}/departments/{dept_id}/head                 — remove head

Branch scoping note
────────────────────
Departments may be org-wide (branch_id is None) or branch-scoped
(branch_id set). For any route acting on an *existing* department, the
branch used for the permission scope check is always the department's
own branch_id — fetched from the DB — never a caller-supplied value.
This prevents a caller from claiming a different branch than the one
the department actually belongs to.
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
    DepartmentDeleteRequest,
    DepartmentDeleteResponse,
    DepartmentHeadAssignResponse,
    DepartmentHeadRemoveResponse,
    DepartmentListResponse,
    DepartmentMemberAssignResponse,
    DepartmentMembersListResponse,
    DepartmentMemberInfo,
    DepartmentResponse,
    DepartmentUpdateRequest,
)
from app.services.department import (
    assign_user_to_department,
    create_department,
    edit_department,
    get_department,
    get_department_members,
    get_departments,
    remove_department_head,
    assign_department_head,
    delete_department,
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
)
async def create_department_route(
    org_id: str,
    body: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.create")),
) -> DepartmentResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments"):
        # body.branch_id is caller-supplied here, which is correct for
        # create: there is no existing row to derive it from yet. A
        # branch-scoped grant can only create within its own branch;
        # require_scoped_permission is responsible for rejecting a
        # mismatch (e.g. branch manager passing a different branch_id,
        # or passing branch_id=None to try to create an org-wide dept).
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.create",
            target_branch_id=body.branch_id,
        )
        department = await create_department(
            session=db,
            organization_id=org_id,
            name=body.name,
            description=body.description,
            avatar_initial=body.avatar_initial,
            created_by=current_user.user_id,
            branch_id=body.branch_id,
        )
        await db.commit()
        return DepartmentResponse.model_validate(department)


# ── List ───────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=DepartmentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List departments in an organization",
)
async def list_departments_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DepartmentListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments"):

        departments = await get_departments(
            session=db,
            organization_id=org_id,
            offset=offset,
            limit=limit,
        )
        items = [DepartmentResponse.model_validate(d) for d in departments]
        return DepartmentListResponse(
            items=items,
            total=len(items),
            offset=offset,
            limit=limit,
        )


# ── Get single ─────────────────────────────────────────────────────────────────

@router.get(
    "/{dept_id}",
    response_model=DepartmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a department by ID",
)
async def get_department_route(
    org_id: str,
    dept_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DepartmentResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}"):
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        return DepartmentResponse.model_validate(department)


# ── Edit ───────────────────────────────────────────────────────────────────────

@router.patch(
    "/{dept_id}",
    response_model=DepartmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a department",
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
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.update",
            target_branch_id=department.branch_id,
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


# ── Delete ─────────────────────────────────────────────────────────────────────

@router.delete(
    "/{dept_id}",
    response_model=DepartmentDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a department",
    description=(
        "Deletes a department. If it has members, "
        "`transfer_to_department_id` is required — all members are moved "
        "atomically before deletion so no user is ever left without a department. "
        "Requires `department.delete`, scoped to the department's own branch "
        "(or org-wide for org-level departments)."
    ),
)
async def delete_department_route(
    org_id: str,
    dept_id: str,
    body: DepartmentDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.delete")),
) -> DepartmentDeleteResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}"):
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.delete",
            target_branch_id=department.branch_id,
        )
        result = await delete_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            transfer_to_department_id=body.transfer_to_department_id,
        )
        await db.commit()
        return DepartmentDeleteResponse(
            **result,
            message=(
                f"Department deleted. {result['members_transferred']} member(s) transferred."
                if result["members_transferred"]
                else "Department deleted."
            ),
        )


# ── Assign user to department ──────────────────────────────────────────────────

@router.put(
    "/{dept_id}/members/{user_id}",
    response_model=DepartmentMemberAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign a user to a department",
    description=(
        "Assigns a user to the specified department. "
        "If the user already belongs to another department, "
        "they are moved atomically — never left without a department. "
        "Requires `department.update`, scoped to the department's own branch "
        "(or org-wide for org-level departments)."
    ),
)
async def assign_user_to_department_route(
    org_id: str,
    dept_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.update")),
) -> DepartmentMemberAssignResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}/members/{user_id}"):
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.update",
            target_branch_id=department.branch_id,
        )
        await assign_user_to_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            user_id=user_id,
        )
        await db.commit()
        return DepartmentMemberAssignResponse(
            user_id=user_id,
            department_id=dept_id,
            organization_id=org_id,
            message="User successfully assigned to department.",
        )


# ── List department members ────────────────────────────────────────────────────

@router.get(
    "/{dept_id}/members",
    response_model=DepartmentMembersListResponse,
    status_code=status.HTTP_200_OK,
    summary="List members of a department",
)
async def list_department_members_route(
    org_id: str,
    dept_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> DepartmentMembersListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}/members"):
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        members, total = await get_department_members(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            offset=offset,
            limit=limit,
        )
        return DepartmentMembersListResponse(
            items=[DepartmentMemberInfo.model_validate(m) for m in members],
            total=total,
            offset=offset,
            limit=limit,
        )


# ── Assign department head ─────────────────────────────────────────────────────

@router.put(
    "/{dept_id}/head/{user_id}",
    response_model=DepartmentHeadAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign a department head",
    description=(
        "Designates a user as the head of this department. "
        "The user must already be a member of the department. "
        "Assigns the `team_leader` system role to them automatically. "
        "Requires `department.update`, scoped to the department's own branch "
        "(or org-wide for org-level departments)."
    ),
)
async def assign_department_head_route(
    org_id: str,
    dept_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.update")),
) -> DepartmentHeadAssignResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}/head/{user_id}"):
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.update",
            target_branch_id=department.branch_id,
        )
        department = await assign_department_head(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            user_id=user_id,
        )
        await db.commit()
        return DepartmentHeadAssignResponse(
            department_id=dept_id,
            organization_id=org_id,
            head_user_id=user_id,
            message="Department head assigned successfully.",
        )


# ── Remove department head ─────────────────────────────────────────────────────

@router.delete(
    "/{dept_id}/head",
    response_model=DepartmentHeadRemoveResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove the department head",
    description=(
        "Clears the department head designation. "
        "By default reverts the former head's role from `team_leader` "
        "back to `member`. "
        "Requires `department.update`, scoped to the department's own branch "
        "(or org-wide for org-level departments)."
    ),
)
async def remove_department_head_route(
    org_id: str,
    dept_id: str,
    revert_role: bool = Query(
        default=True,
        description="If true, reverts the former head's role to 'member'.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("department.update")),
) -> DepartmentHeadRemoveResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/departments/{dept_id}/head"):
        department = await get_department(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
        )
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="department.update",
            target_branch_id=department.branch_id,
        )
        await remove_department_head(
            session=db,
            organization_id=org_id,
            department_id=dept_id,
            revert_role=revert_role,
        )
        await db.commit()
        return DepartmentHeadRemoveResponse(
            department_id=dept_id,
            organization_id=org_id,
            message="Department head removed successfully.",
        )