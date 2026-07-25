"""
app/routers/branches.py

Branch management routes.

Permission model:
  1. require_permission("branch.<action>") — flat dependency, cheap early exit.
  2. require_same_organization() — tenant isolation; sync, no DB.
  3. require_scoped_permission(...) — authoritative scope check.
     super_admin bypasses automatically.
  4. Service call.

Special case — member transfer permission scope:
  The assign/transfer endpoint checks branch.update against the user's
  CURRENT (source) branch, not the destination. A branch manager has
  authority over the members IN their branch, so they decide who leaves —
  not who arrives. An org-wide grant covers the check regardless of
  source branch (super_admin also bypasses).

Routes:
  POST   /organizations/{org_id}/branches                              — create
  GET    /organizations/{org_id}/branches                              — list
  GET    /organizations/{org_id}/branches/{branch_id}                  — get one
  PATCH  /organizations/{org_id}/branches/{branch_id}                  — edit name
  DELETE /organizations/{org_id}/branches/{branch_id}                  — delete (must be pre-emptied)
  GET    /organizations/{org_id}/branches/{branch_id}/members           — list members
  PUT    /organizations/{org_id}/branches/{branch_id}/members/{user_id} — transfer user
  PUT    /organizations/{org_id}/branches/{branch_id}/manager           — assign manager
  DELETE /organizations/{org_id}/branches/{branch_id}/manager           — unassign manager
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_permission,
    require_scoped_permission,
    require_same_organization,
    require_active_subscription,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import User
from app.schemas.branch import (
    BranchCreateRequest,
    BranchListResponse,
    BranchManagerAssignRequest,
    BranchManagerResponse,
    BranchMemberAssignRequest,
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
        await require_active_subscription(db =db, current_user=current_user)
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
        "The Headquarters branch cannot be renamed. "
        "Requires `branch.update` scoped to this branch or org-wide."
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
        "Deletes a branch. The branch must be fully empty before this "
        "call will succeed — pre-empty it in this order:\n\n"
        "1. Unassign the manager (DELETE /branches/{branch_id}/manager).\n"
        "2. Delete every department via DELETE /departments/{dept_id} "
        "— use `transfer_to_department_id` on each call to move that "
        "department's members atomically to a department in another branch.\n"
        "3. Once no manager, no departments, and no members remain, this "
        "call deletes the branch cleanly.\n\n"
        "The Headquarters branch can never be deleted. "
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
    summary="Transfer a user to this branch",
    description=(
        "Transfers a user to this branch and places them into the specified "
        "department within it. Both happen atomically — a user's department "
        "must always belong to their home branch.\n\n"
        "Permission scope: `branch.update` is checked against the user's "
        "CURRENT (source) branch, not the destination. A branch manager "
        "decides who leaves their branch; they do not need authority over "
        "the receiving branch. An org-wide grant covers any transfer."
    ),
)
async def assign_user_to_branch_route(
    org_id: str,
    branch_id: str,
    user_id: str,
    body: BranchMemberAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("branch.update")),
) -> BranchMemberAssignResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(
        db, "/organizations/{org_id}/branches/{branch_id}/members/{user_id}"
    ):
        # ── Resolve the user's current (source) branch ────────────────────
        # Permission is checked on the source branch — the manager of the
        # branch the user CURRENTLY belongs to is the one who authorises
        # the move, not the manager of the receiving branch.
        # A 404 here is a pre-flight — the service will also validate, but
        # we need the source branch_id before we can run the scope check.
        user_result = await db.execute(
            select(User).where(
                User.id == user_id,
                User.organization_id == org_id,
            )
        )
        target_user = user_result.scalar_one_or_none()
        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found or does not belong to this organization.",
            )

        # ── Scope check: source branch, not destination ───────────────────
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="branch.update",
            target_branch_id=target_user.branch_id,  # SOURCE branch
        )

        await assign_user_to_branch(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,          # destination
            user_id=user_id,
            department_id=body.department_id,
        )
        await db.commit()

        return BranchMemberAssignResponse(
            user_id=user_id,
            branch_id=branch_id,
            organization_id=org_id,
            message="User successfully transferred to branch.",
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