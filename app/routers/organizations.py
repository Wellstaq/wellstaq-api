"""
app/routers/organization.py

Organization management routes.

Routes:
  POST  /organizations/{org_id}/invites                       — send an invite
  GET   /organizations/{org_id}/invites                       — list invites
  POST  /organizations/{org_id}/invites/{invite_code}/accept  — accept an invite (no auth)
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    require_permission,
    require_same_organization,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import OrganizationInvite
from app.schemas.organization import (
    InviteItem,
    InviteListResponse,
    SendInviteRequest,
    SendInviteResponse,
)
from app.services.organization.invite_service import (
    send_invite,
)

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
        "Requires `member.invite` permission."
    ),
)
async def send_invite_route(
    org_id: str,
    body: SendInviteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.invite")),
) -> SendInviteResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/invites"):
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
        query = (
            select(OrganizationInvite)
            .where(OrganizationInvite.organization_id == org_id)
            .order_by(OrganizationInvite.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        if status_filter is not None:
            query = query.where(OrganizationInvite.status == status_filter)

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
            total=len(items),
            offset=offset,
            limit=limit,
        )

