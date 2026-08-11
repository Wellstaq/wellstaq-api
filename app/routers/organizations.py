"""
app/routers/organizations.py

Organization management routes.

Routes:
  POST  /organizations/{org_id}/invites                       — send an invite
  GET   /organizations/{org_id}/invites                       — list invites
  POST  /organizations/{org_id}/invites/{invite_code}/accept  — accept an invite (no auth)
  GET   /organizations/{org_id}/members                       — list all org members
  GET   /organizations/{org_id}/clubs                         — list all org clubs

Note on branches/departments:
  There is intentionally NO GET /organizations/{org_id}/branches or
  GET /organizations/{org_id}/departments here — those already exist in
  app/routers/branches.py and app/routers/departments.py respectively.
  Adding them again in this file would register two routers against the
  same path template, which is ambiguous and risks two different
  permission checks silently guarding what looks like "one" endpoint.
  Use the existing endpoints for those two resources.
"""

from __future__ import annotations

import logging
from typing import Literal
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_permission,
    require_same_organization,
    require_scoped_permission,
    require_active_subscription,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import OrganizationInvite
from app.schemas.organization import (
    BranchStatsResponse,
    InviteItem,
    InviteListResponse,
    OrganizationClubInfo,
    OrganizationClubsListResponse,
    OrganizationMemberInfo,
    OrganizationMembersListResponse,
    OrgStatsResponse,
    SendInviteRequest,
    SendInviteResponse,
)
from app.services.organization.invite_service import send_invite
from app.services.organization.org_directory import (
    get_organization_clubs,
    get_organization_members,
)

from app.services.organization.org_stats import get_branch_stats, get_org_stats

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/organizations/{org_id}",
    tags=["organizations"],
)


# ── Send invite ────────────────────────────────────────────────────────────────

@router.post(
    "/invites",
    response_model=SendInviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send an organization invite",
    description=(
        "Invite a new user to join the organization. "
        "The invitee receives an email with a secure invite link valid for 7 days. "
        "Requires `member.invite` permission either on org scope or branch scope."
    ),
)
async def send_invite_route(
    org_id: str,
    body: SendInviteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("member.invite")),
) -> SendInviteResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/invites"):

        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="member.invite",
            target_branch_id=body.branch_id,
        )
        invite = await send_invite(
            db,
            org_id=org_id,
            invited_by_user_id=current_user.user_id,
            invited_by_role=current_user.role,
            invited_by_branch_id=current_user.branch_id,
            invited_by_permissions=current_user.permissions,
            invited_email=body.invited_email,
            invited_department_id=body.invited_department_id,
            invited_role_id=body.invited_role_id,
            branch_id=body.branch_id,
        )

        await db.commit()
        await db.refresh(invite)

        return SendInviteResponse(
            invite_id=str(invite.id),
            invited_email=invite.invited_email,
            organization_id=str(invite.organization_id),
            branch_id=str(invite.branch_id) if invite.branch_id else None,
            invited_department_id=str(invite.invited_department_id),
            invited_role_id=str(invite.invited_role_id),
            status=invite.status,
            expires_at=invite.expires_at.isoformat(),
        )


# ── List invites ───────────────────────────────────────────────────────────────

@router.get(
    "/invites",
    response_model=InviteListResponse,
    status_code=status.HTTP_200_OK,
    summary="List organization invites",
    description=(
        "Returns invites for the organization. "
        "Filter by status: pending, accepted, expired, cancelled. "
        "Omit status to return all. "
        "Requires `member.invite` permission."
    ),
)
async def list_invites_route(
    org_id: str,
    status_filter: Literal["pending", "accepted", "expired", "cancelled"] | None = Query(
        default=None,
        alias="status",
        description="Filter by invite status. Omit to return all.",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.invite")),
) -> InviteListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/invites"):
        filters = [OrganizationInvite.organization_id == org_id]
        if status_filter is not None:
            filters.append(OrganizationInvite.status == status_filter)

        total = (await db.execute(
            select(func.count()).select_from(OrganizationInvite).where(*filters)
        )).scalar_one()

        query = (
            select(OrganizationInvite)
            .where(*filters)
            .order_by(OrganizationInvite.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await db.execute(query)
        invites = result.scalars().all()

        items = [
            InviteItem(
                invite_id=str(i.id),
                invited_email=i.invited_email,
                organization_id=str(i.organization_id),
                branch_id=str(i.branch_id) if i.branch_id else None,
                invited_department_id=str(i.invited_department_id),
                invited_role_id=str(i.invited_role_id),
                invited_by=str(i.invited_by),
                status=i.status,
                expires_at=i.expires_at.isoformat(),
                created_at=i.created_at.isoformat(),
            )
            for i in invites
        ]

        return InviteListResponse(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
        )


@router.delete(
    "/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Delete a pending organization invite",
    description="Deletes an invite only while it is still pending. Accepted or otherwise used invites are retained for audit history.",
)
async def delete_pending_invite_route(
    org_id: str,
    invite_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("member.invite")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/invites/{invite_id}"):
        result = await db.execute(
            select(OrganizationInvite).where(
                OrganizationInvite.id == invite_id,
                OrganizationInvite.organization_id == org_id,
            )
        )
        invite = result.scalar_one_or_none()
        if invite is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="member.invite",
            target_branch_id=invite.branch_id,
        )
        if invite.status != "pending":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Only pending invitations can be deleted. This invitation has already been used or closed and must remain in the audit history.",
            )
        await db.delete(invite)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Members ──────────────────────────────────────────────────────────────────

@router.get(
    "/members",
    response_model=OrganizationMembersListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all members of the organization",
    description=(
        "Returns every user belonging to the organization, across all "
        "branches and departments. Accessible to any authenticated "
        "member of the organization."
    ),
)
async def list_organization_members_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> OrganizationMembersListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/members"):
        members, total = await get_organization_members(
            session=db, organization_id=org_id, offset=offset, limit=limit,
        )
        return OrganizationMembersListResponse(
            items=[OrganizationMemberInfo.model_validate(m) for m in members],
            total=total,
            offset=offset,
            limit=limit,
        )


# ── Clubs ────────────────────────────────────────────────────────────────────

@router.get(
    "/clubs",
    response_model=OrganizationClubsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all clubs in the organization",
    description=(
        "Returns every club belonging to the organization, across all "
        "branches. Accessible to any authenticated member of the "
        "organization."
    ),
)
async def list_organization_clubs_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> OrganizationClubsListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/clubs"):
        clubs, total = await get_organization_clubs(
            session=db, organization_id=org_id, offset=offset, limit=limit,
        )
        return OrganizationClubsListResponse(
            items=[OrganizationClubInfo.model_validate(c) for c in clubs],
            total=total,
            offset=offset,
            limit=limit,
        )

# ── Stats ────────────────────────────────────────────────────────────────────
@router.get(
    "/stats",
    response_model=OrgStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get organization-wide structural stats",
    description=(
        "Returns live counts (total users, branches, departments, clubs, "
        "events, challenges, posts) for the organization. Computed fresh "
        "on every request, not snapshotted. Accessible to only the admin with org_level permissoin."
    ),
)
async def org_stats_route(
    org_id: str,
    user_status: Literal["pending", "active", "inactive", "suspended"] | None = Query(default=None),
    challenge_status: Literal["draft", "active", "completed", "cancelled", "archived"] | None = Query(default=None),
    event_status: Literal["scheduled", "ongoing", "completed", "cancelled"] | None = Query(default=None),
    period: Literal["week", "month", "three_months", "six_months", "nine_months", "year", "custom"] | None = Query(
        default=None, description="Omit for all-time stats (default). Set to compare against a baseline for trend %.",
    ),
    start_date: date | None = Query(default=None, description="Required when period='custom'."),
    end_date: date | None = Query(default=None, description="Optional, defaults to today when period='custom'."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> OrgStatsResponse:
    require_same_organization(org_id, current_user)
    if period == "custom" and start_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date is required when period='custom'.")

    async with request_guard(db, "/organizations/{org_id}/stats"):
        await require_scoped_permission(db=db, current_user=current_user, permission="org.stats", target_branch_id=None)
        stats = await get_org_stats(
            session=db, organization_id=org_id,
            user_status=user_status, challenge_status=challenge_status, event_status=event_status,
            period=period, start_date=start_date, end_date=end_date,
        )
        return OrgStatsResponse(**stats)

@router.get(
    "/branches/{branch_id}/stats",
    response_model=BranchStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get branch-level structural stats",
    description=(
        "Returns live counts (users, departments, clubs, events) scoped "
        "to one branch. Own branch always accessible; another branch "
        "requires super_admin."
    ),
)
async def branch_stats_route(
    org_id: str, branch_id: str,
    user_status: Literal["pending", "active", "inactive", "suspended"] | None = Query(default=None),
    challenge_status: Literal["upcoming", "active", "completed", "cancelled", "archived"] | None = Query(default=None),
    event_status: Literal["scheduled", "ongoing", "completed", "cancelled"] | None = Query(default=None),
    period: Literal["week", "month", "three_months", "six_months", "nine_months", "year", "custom"] | None = Query(default=None),
    start_date: date | None = Query(default=None, description="Required when period='custom'."),
    end_date: date | None = Query(default=None, description="Optional, defaults to today when period='custom'."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> BranchStatsResponse:
    require_same_organization(org_id, current_user)
    if current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's stats.")
    if period == "custom" and start_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date is required when period='custom'.")

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/stats"):
        await require_scoped_permission(db=db, current_user=current_user, permission="org.stats", target_branch_id=branch_id)
        stats = await get_branch_stats(
            session=db, organization_id=org_id, branch_id=branch_id,
            user_status=user_status, challenge_status=challenge_status, event_status=event_status,
            period=period, start_date=start_date, end_date=end_date,
        )
        return BranchStatsResponse(**stats)
