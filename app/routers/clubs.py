"""
app/routers/clubs.py

Club management routes.

Routes:
  POST   /organizations/{org_id}/branches/{branch_id}/clubs              — create (any branch member)
  GET    /organizations/{org_id}/clubs                                    — list org-wide
  GET    /organizations/{org_id}/branches/{branch_id}/clubs              — list by branch
  GET    /organizations/{org_id}/clubs/{club_id}                         — get one
  PATCH  /organizations/{org_id}/clubs/{club_id}                         — edit (leader or club.update)
  DELETE /organizations/{org_id}/clubs/{club_id}                         — delete (leader, branch manager with branch-scoped club.delete, or org-wide club.delete)
  PUT    /organizations/{org_id}/clubs/{club_id}/members/{user_id}       — join (branch members only)
  DELETE /organizations/{org_id}/clubs/{club_id}/members/{user_id}/leave — leave (self only)
  DELETE /organizations/{org_id}/clubs/{club_id}/members/{user_id}       — remove (leader only)
  GET    /organizations/{org_id}/clubs/{club_id}/members                 — list members
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_same_organization,
    require_scoped_permission,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.club import (
    ClubCreateRequest,
    ClubDeleteResponse,
    ClubJoinResponse,
    ClubLeaveResponse,
    ClubListResponse,
    ClubMemberInfo,
    ClubMembersListResponse,
    ClubRemoveMemberResponse,
    ClubResponse,
    ClubUpdateRequest,
)
from app.services.club import (
    create_club,
    delete_club,
    edit_club,
    get_branch_clubs,
    get_club,
    get_club_members,
    get_clubs,
    join_club,
    leave_club,
    remove_club_member,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clubs"])


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post(
    "/organizations/{org_id}/branches/{branch_id}/clubs",
    response_model=ClubResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a club",
    description=(
        "Any authenticated user can create a club, but they must belong to "
        "the branch they are creating it under. The creator automatically "
        "becomes the club leader and is added as the first member."
    ),
)
async def create_club_route(
    org_id: str,
    branch_id: str,
    body: ClubCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/clubs"):
        club = await create_club(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
            name=body.name,
            description=body.description,
            privacy=body.privacy,
            created_by=current_user.user_id,
        )
        await db.commit()
        return ClubResponse.model_validate(club)


# ── List org-wide ──────────────────────────────────────────────────────────────

@router.get(
    "/organizations/{org_id}/clubs",
    response_model=ClubListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all clubs in organization",
)
async def list_clubs_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    privacy: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/clubs"):
        clubs, total = await get_clubs(
            session=db,
            organization_id=org_id,
            offset=offset,
            limit=limit,
            privacy=privacy,
        )
        return ClubListResponse(
            items=[ClubResponse.model_validate(c) for c in clubs],
            total=total,
            offset=offset,
            limit=limit,
        )


# ── List by branch ─────────────────────────────────────────────────────────────

@router.get(
    "/organizations/{org_id}/branches/{branch_id}/clubs",
    response_model=ClubListResponse,
    status_code=status.HTTP_200_OK,
    summary="List clubs in a branch",
)
async def list_branch_clubs_route(
    org_id: str,
    branch_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    privacy: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/branches/{branch_id}/clubs"):
        clubs, total = await get_branch_clubs(
            session=db,
            organization_id=org_id,
            branch_id=branch_id,
            offset=offset,
            limit=limit,
            privacy=privacy,
        )
        return ClubListResponse(
            items=[ClubResponse.model_validate(c) for c in clubs],
            total=total,
            offset=offset,
            limit=limit,
        )


# ── Get one ────────────────────────────────────────────────────────────────────

@router.get(
    "/organizations/{org_id}/clubs/{club_id}",
    response_model=ClubResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a club by ID",
)
async def get_club_route(
    org_id: str,
    club_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/clubs/{club_id}"):
        club = await get_club(session=db, organization_id=org_id, club_id=club_id)
        return ClubResponse.model_validate(club)


# ── Edit ───────────────────────────────────────────────────────────────────────

@router.patch(
    "/organizations/{org_id}/clubs/{club_id}",
    response_model=ClubResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a club",
    description=(
        "Club leader can edit their own club freely. "
        "Admins with `club.update` org-wide or branch-scoped can edit any club "
        "within their scope."
    ),
)
async def edit_club_route(
    org_id: str,
    club_id: str,
    body: ClubUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/clubs/{club_id}"):
        club = await get_club(session=db, organization_id=org_id, club_id=club_id)

        is_leader = club.leader_id == current_user.user_id
        has_permission = current_user.has_permission("club.update")

        if not is_leader and not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the club leader or a user with 'club.update' can edit this club.",
            )

        club = await edit_club(
            session=db,
            organization_id=org_id,
            club_id=club_id,
            name=body.name,
            description=body.description,
            privacy=body.privacy,
        )
        await db.commit()
        return ClubResponse.model_validate(club)


# ── Delete ─────────────────────────────────────────────────────────────────────

@router.delete(
    "/organizations/{org_id}/clubs/{club_id}",
    response_model=ClubDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a club",
    description=(
        "Deletes a club and all its members (cascade). "
        "Allowed for: the club leader, a user with `club.delete` org-wide, "
        "or a branch manager with `club.delete` scoped to the club's branch. "
        "Branch-scoped grants are resolved via require_scoped_permission — "
        "no separate permission name needed."
    ),
)
async def delete_club_route(
    org_id: str,
    club_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubDeleteResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/clubs/{club_id}"):
        # Load club first — need branch_id for the scoped permission check
        club = await get_club(session=db, organization_id=org_id, club_id=club_id)

        is_leader = club.leader_id == current_user.user_id

        if not is_leader:
            # require_scoped_permission accepts both org-wide grants (branch_id=None)
            # AND grants scoped to this specific branch — so a branch manager with
            # club.delete for branch X can delete clubs in branch X without needing
            # an org-wide grant. super_admin bypasses automatically.
            await require_scoped_permission(
                db=db,
                current_user=current_user,
                permission="club.delete",
                target_branch_id=club.branch_id,
            )

        result = await delete_club(
            session=db,
            organization_id=org_id,
            club_id=club_id,
        )
        await db.commit()
        return ClubDeleteResponse(**result, message="Club deleted successfully.")


# ── Join ───────────────────────────────────────────────────────────────────────

@router.put(
    "/organizations/{org_id}/clubs/{club_id}/members/{user_id}",
    response_model=ClubJoinResponse,
    status_code=status.HTTP_200_OK,
    summary="Join a club",
    description=(
        "Join a public club. User must belong to the branch that owns the club. "
        "Private clubs require an invite — this endpoint rejects them. "
        "Users can only join on their own behalf unless they hold `club.update`."
    ),
)
async def join_club_route(
    org_id: str,
    club_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubJoinResponse:
    require_same_organization(org_id, current_user)

    is_self = current_user.user_id == user_id
    has_permission = current_user.has_permission("club.update")

    if not is_self and not has_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only join clubs on your own behalf unless you hold 'club.update'.",
        )

    async with request_guard(db, "/organizations/{org_id}/clubs/{club_id}/members/{user_id}"):
        await join_club(
            session=db,
            organization_id=org_id,
            club_id=club_id,
            user_id=user_id,
        )
        await db.commit()
        return ClubJoinResponse(
            user_id=user_id,
            club_id=club_id,
            organization_id=org_id,
            message="User successfully joined the club.",
        )


# ── Leave (self) ───────────────────────────────────────────────────────────────

@router.delete(
    "/organizations/{org_id}/clubs/{club_id}/members/{user_id}/leave",
    response_model=ClubLeaveResponse,
    status_code=status.HTTP_200_OK,
    summary="Leave a club",
    description=(
        "A user leaves a club they are a member of. "
        "Club leader cannot leave — they must delete the club instead."
    ),
)
async def leave_club_route(
    org_id: str,
    club_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubLeaveResponse:
    require_same_organization(org_id, current_user)

    if current_user.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only leave a club on your own behalf.",
        )

    async with request_guard(
        db, "/organizations/{org_id}/clubs/{club_id}/members/{user_id}/leave"
    ):
        await leave_club(
            session=db,
            organization_id=org_id,
            club_id=club_id,
            user_id=user_id,
        )
        await db.commit()
        return ClubLeaveResponse(
            user_id=user_id,
            club_id=club_id,
            organization_id=org_id,
            message="User successfully left the club.",
        )


# ── Remove member (leader only) ────────────────────────────────────────────────

@router.delete(
    "/organizations/{org_id}/clubs/{club_id}/members/{user_id}",
    response_model=ClubRemoveMemberResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove a club member",
    description=(
        "Club leader removes a member from their club. "
        "No admin permission required — the leader owns the club. "
        "Leader cannot remove themselves via this endpoint."
    ),
)
async def remove_club_member_route(
    org_id: str,
    club_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubRemoveMemberResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(
        db, "/organizations/{org_id}/clubs/{club_id}/members/{user_id}"
    ):
        await remove_club_member(
            session=db,
            organization_id=org_id,
            club_id=club_id,
            target_user_id=user_id,
            requesting_user_id=current_user.user_id,
        )
        await db.commit()
        return ClubRemoveMemberResponse(
            user_id=user_id,
            club_id=club_id,
            organization_id=org_id,
            message="Member removed from club.",
        )


# ── List members ───────────────────────────────────────────────────────────────

@router.get(
    "/organizations/{org_id}/clubs/{club_id}/members",
    response_model=ClubMembersListResponse,
    status_code=status.HTTP_200_OK,
    summary="List club members",
)
async def list_club_members_route(
    org_id: str,
    club_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ClubMembersListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/clubs/{club_id}/members"):
        members, total = await get_club_members(
            session=db,
            organization_id=org_id,
            club_id=club_id,
            offset=offset,
            limit=limit,
        )
        return ClubMembersListResponse(
            items=[ClubMemberInfo.model_validate(m) for m in members],
            total=total,
            offset=offset,
            limit=limit,
        )