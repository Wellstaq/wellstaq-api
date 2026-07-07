"""
app/routers/branches.py

Branch management routes.

Permission model mirrors departments.py:
  1. require_permission("branch.<action>") — flat dependency, cheap early exit.
  2. require_same_organization() — tenant isolation; sync, no DB.
  3. require_scoped_permission(..., target_branch_id=None) — authoritative
     org-wide check. super_admin bypasses automatically.
  4. Service call.

Routes:
  POST   /organizations/{org_id}/branches                              — create
  GET    /organizations/{org_id}/branches                              — list
  GET    /organizations/{org_id}/branches/{branch_id}                  — get one
  PATCH  /organizations/{org_id}/branches/{branch_id}                  — edit
  DELETE /organizations/{org_id}/branches/{branch_id}                  — delete (must be empty)
  GET    /organizations/{org_id}/branches/{branch_id}/members           — list members
  PUT    /organizations/{org_id}/branches/{branch_id}/members/{user_id} — assign/transfer user
  PUT    /organizations/{org_id}/branches/{branch_id}/manager           — assign manager
  DELETE /organizations/{org_id}/branches/{branch_id}/manager           — unassign manager
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
from app.schemas.branch import (
    BranchCreateRequest,
    BranchListResponse,
    BranchManagerAssignRequest,
    BranchManagerResponse,
    BranchMemberAssignResponse,
    BranchMemberInfo,
    BranchMembersListResponse,
    BranchResponse,
    BranchUpdateRequest,
)
from app.services.branch import (
    assign_branch_manager,
    assign_user_to_branch,
    create_branch,
    delete_branch,
    edit_branch,
    get_branch,
    get_branch_members,
    get_branches,
    unassign_branch_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/organizations/{org_id}/branches",
    tags=["branches"],
)


# ── Create ───────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=BranchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a branch",
    description=(
        "Creates a new branch under the organization, with no manager "
        "assigned. Assign a manager afterward via "
        "PUT /branches/{branch_id}/manager. Requires `branch.create` "
        "granted org-wide."
    ),
)
async def create_branch_route(
    org_id: str,
    body: BranchCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.create")),
) -> BranchResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.create",
            target_branch_id=None,
        )

        branch = await create_branch(
            session=db,
            organization_id=org_id,
            name=body.name,
            created_by=current_user.user_id,
        )
        await db.commit()
        return BranchResponse.model_validate(branch)


# ── List ─────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=BranchListResponse,
    status_code=status.HTTP_200_OK,
    summary="List branches in an organization",
    description="Accessible to any authenticated member of the organization.",
)
async def list_branches_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> BranchListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches"):
        branches, total = await get_branches(
            session=db, organization_id=org_id, offset=offset, limit=limit,
        )
        return BranchListResponse(
            items=[BranchResponse.model_validate(b) for b in branches],
            total=total,
            offset=offset,
            limit=limit,
        )


# ── Get one ──────────────────────────────────────────────────────────────────

@router.get(
    "/{branch_id}",
    response_model=BranchResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a branch by ID",
    description="Accessible to any authenticated member of the organization.",
)
async def get_branch_route(
    org_id: str,
    branch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> BranchResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}"):
        branch = await get_branch(session=db, organization_id=org_id, branch_id=branch_id)
        return BranchResponse.model_validate(branch)


# ── Edit ─────────────────────────────────────────────────────────────────────

@router.patch(
    "/{branch_id}",
    response_model=BranchResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a branch",
    description=(
        "Partially updates a branch's name. Manager assignment is handled "
        "separately via PUT/DELETE /branches/{branch_id}/manager. "
        "Requires `branch.update` granted org-wide."
    ),
)
async def edit_branch_route(
    org_id: str,
    branch_id: str,
    body: BranchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.update")),
) -> BranchResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.update",
            target_branch_id=branch_id,
        )

        branch = await edit_branch(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
            name=body.name,
        )
        await db.commit()
        return BranchResponse.model_validate(branch)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete(
    "/{branch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a branch",
    description=(
        "Deletes a branch. The branch must be fully empty before deletion "
        "is allowed:\n\n"
        "1. Unassign the manager first (DELETE /branches/{branch_id}/manager).\n"
        "2. Delete every department in this branch first via DELETE "
        "/departments/{dept_id} — pass `transfer_to_department_id` on each "
        "call to move that department's members to a department in another "
        "branch atomically.\n"
        "3. Once the branch has no manager, no departments, and no members, "
        "this call succeeds.\n\n"
        "The Headquarters branch cannot be deleted. "
        "Requires `branch.delete` granted org-wide."
    ),
)
async def delete_branch_route(
    org_id: str,
    branch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.delete")),
):
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}"):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.delete",
            target_branch_id=None,
        )

        await delete_branch(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
        )
        await db.commit()


# ── Members ──────────────────────────────────────────────────────────────────

@router.get(
    "/{branch_id}/members",
    response_model=BranchMembersListResponse,
    status_code=status.HTTP_200_OK,
    summary="List members of a branch",
    description="Accessible to any authenticated member of the organization.",
)
async def list_branch_members_route(
    org_id: str,
    branch_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> BranchMembersListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/members"):
        members, total = await get_branch_members(
            session=db, organization_id=org_id, branch_id=branch_id,
            offset=offset, limit=limit,
        )
        return BranchMembersListResponse(
            items=[BranchMemberInfo.model_validate(m) for m in members],
            total=total,
            offset=offset,
            limit=limit,
        )


@router.put(
    "/{branch_id}/members/{user_id}",
    response_model=BranchMemberAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign (or transfer) a user to a branch",
    description=(
        "Assigns a user to this branch. If they already belong to another "
        "branch, they are transferred (a user can only be in one branch at "
        "a time). Requires `branch.update` granted org-wide."
    ),
)
async def assign_user_to_branch_route(
    org_id: str,
    branch_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.update")),
) -> BranchMemberAssignResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(
        db, "/organizations/{org_id}/branches/{branch_id}/members/{user_id}"
    ):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.update",
            target_branch_id=branch_id,
        )

        await assign_user_to_branch(
            session=db, organization_id=org_id, branch_id=branch_id, user_id=user_id,
        )
        await db.commit()

        return BranchMemberAssignResponse(
            user_id=user_id,
            branch_id=branch_id,
            organization_id=org_id,
            message="User successfully assigned to branch.",
        )


# ── Manager ──────────────────────────────────────────────────────────────────

@router.put(
    "/{branch_id}/manager",
    response_model=BranchManagerResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign a branch manager",
    description=(
        "Assigns a user as manager of this branch. Fails if the branch "
        "already has a manager — unassign first. Requires "
        "`branch.manager.assign` granted org-wide."
    ),
)
async def assign_branch_manager_route(
    org_id: str,
    branch_id: str,
    body: BranchManagerAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.manager.assign")),
) -> BranchManagerResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(
        db, "/organizations/{org_id}/branches/{branch_id}/manager"
    ):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.manager.assign",
            target_branch_id=None,
        )

        branch = await assign_branch_manager(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
            manager_id=body.manager_id,
        )
        await db.commit()

        return BranchManagerResponse(
            branch_id=branch.id,
            organization_id=org_id,
            manager_id=branch.manager_id,
            message="Manager assigned successfully.",
        )


@router.delete(
    "/{branch_id}/manager",
    response_model=BranchManagerResponse,
    status_code=status.HTTP_200_OK,
    summary="Unassign the branch manager",
    description=(
        "Removes the current manager from this branch. Required before "
        "the branch can be deleted. Requires `branch.manager.assign` "
        "granted org-wide."
    ),
)
async def unassign_branch_manager_route(
    org_id: str,
    branch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.manager.assign")),
) -> BranchManagerResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(
        db, "/organizations/{org_id}/branches/{branch_id}/manager"
    ):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.manager.assign",
            target_branch_id=None,
        )

        branch = await unassign_branch_manager(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
        )
        await db.commit()

        return BranchManagerResponse(
            branch_id=branch.id,
            organization_id=org_id,
            manager_id=branch.manager_id,  # None after unassign
            message="Manager unassigned successfully.",
        )