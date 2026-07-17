"""
app/routers/challenges.py

Permission model:
  challenge.create / .update / .delete — scope='both', checked against
  the challenge's branch_id (None = org-wide grant required), same
  pattern as events.

  Visibility (list/get) — no challenge.view_all permission exists in the
  seed; a user sees org-wide challenges + their own branch's challenges
  only. See check_challenge_visibility.

  Join/leave/progress — self-service, gated only by visibility (can't
  join or submit progress for a challenge you can't see).

  mark_participant_complete — manual admin override via challenge.update,
  see TODO(background-job) in participant_service.py. Automatic
  completion (cumulative target crossed) happens inline in
  progress_service.submit_progress instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_permission, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import User
from app.schemas.challenge import (
    ChallengeCreateRequest,
    ChallengeListResponse,
    ChallengeResponse,
    ChallengeUpdateRequest,
    ParticipantCompleteRequest,
    ParticipantInfo,
    ParticipantJoinResponse,
    ParticipantListResponse,
    ProgressEntryResponse,
    ProgressSubmitRequest,
)
from app.services.challenge import (
    create_challenge,
    delete_challenge,
    edit_challenge,
    get_challenge,
    get_challenges,
    get_participants,
    join_challenge,
    leave_challenge,
    mark_participant_complete,
)
from app.services.challenge._helpers import check_challenge_scope, check_challenge_visibility
from app.services.challenge.progress_service import submit_progress

router = APIRouter(prefix="/organizations/{org_id}/challenges", tags=["challenges"])


# ── Create ───────────────────────────────────────────────────────────────────

@router.post("", response_model=ChallengeResponse, status_code=status.HTTP_201_CREATED)
async def create_challenge_route(
    org_id: str, body: ChallengeCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.create")),
) -> ChallengeResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges"):
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.create", branch_id=body.branch_id)
        challenge = await create_challenge(
            session=db, organization_id=org_id, branch_id=body.branch_id,
            wellbeing_challenge_id=body.wellbeing_challenge_id, name=body.name, description=body.description,
            start_date=body.start_date, end_date=body.end_date, metric_type=body.metric_type,
            target_type=body.target_type, target_value=body.target_value,
            created_by=current_user.user_id,
        )
        await db.commit()
        return ChallengeResponse.model_validate(challenge)


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=ChallengeListResponse, status_code=status.HTTP_200_OK)
async def list_challenges_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ChallengeListResponse:
    require_same_organization(org_id, current_user)
    if branch_id is not None and current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's challenges.")

    async with request_guard(db, "/organizations/{org_id}/challenges"):
        challenges, total = await get_challenges(
            session=db, organization_id=org_id, branch_id=branch_id,
            viewer_branch_id=current_user.branch_id, is_super_admin=(current_user.role == "super_admin"),
            offset=offset, limit=limit,
        )
        return ChallengeListResponse(
            items=[ChallengeResponse.model_validate(c) for c in challenges], total=total, offset=offset, limit=limit,
        )


# ── Get one ──────────────────────────────────────────────────────────────────

@router.get("/{challenge_id}", response_model=ChallengeResponse, status_code=status.HTTP_200_OK)
async def get_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ChallengeResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        check_challenge_visibility(current_user=current_user, challenge=challenge)
        return ChallengeResponse.model_validate(challenge)


# ── Edit ─────────────────────────────────────────────────────────────────────

@router.patch("/{challenge_id}", response_model=ChallengeResponse, status_code=status.HTTP_200_OK)
async def edit_challenge_route(
    org_id: str, challenge_id: str, body: ChallengeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.update")),
) -> ChallengeResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)
        updated = await edit_challenge(
            session=db, organization_id=org_id, challenge_id=challenge_id,
            name=body.name, description=body.description, start_date=body.start_date, end_date=body.end_date,
            metric_type=body.metric_type, target_type=body.target_type, target_value=body.target_value,
            status_=body.status,
        )
        await db.commit()
        return ChallengeResponse.model_validate(updated)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{challenge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.delete")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.delete", branch_id=challenge.branch_id)
        await delete_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await db.commit()


# ── Join / Leave ─────────────────────────────────────────────────────────────

@router.post("/{challenge_id}/join", response_model=ParticipantJoinResponse, status_code=status.HTTP_201_CREATED)
async def join_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantJoinResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/join"):
        await join_challenge(session=db, organization_id=org_id, challenge_id=challenge_id, current_user=current_user)
        await db.commit()
        return ParticipantJoinResponse(challenge_id=challenge_id, user_id=current_user.user_id, message="Joined challenge.")


@router.delete("/{challenge_id}/join", status_code=status.HTTP_204_NO_CONTENT)
async def leave_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/join"):
        await leave_challenge(session=db, organization_id=org_id, challenge_id=challenge_id, current_user=current_user)
        await db.commit()


# ── Progress ─────────────────────────────────────────────────────────────────

@router.post(
    "/{challenge_id}/progress",
    response_model=ProgressEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit today's progress",
    description=(
        "Upserts today's (WAT) progress entry for the caller and checks "
        "for automatic completion (cumulative challenges only — see "
        "TODO(background-job) for daily_minimum finalization). "
        "entry_date is always computed server-side, never accepted from "
        "the client."
    ),
)
async def submit_progress_route(
    org_id: str, challenge_id: str, body: ProgressSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ProgressEntryResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/progress"):
        entry = await submit_progress(
            session=db, organization_id=org_id, challenge_id=challenge_id,
            user_id=current_user.user_id, value=body.value, source=body.source,
        )
        await db.commit()

        if entry is None:
            return ProgressEntryResponse(
                entry_date=None, value=body.value, source=body.source,
                already_completed=True, message="Challenge already completed — progress not recorded.",
            )
        return ProgressEntryResponse(entry_date=entry.entry_date, value=entry.value, source=entry.source)


# ── Participants ─────────────────────────────────────────────────────────────

@router.get("/{challenge_id}/participants", response_model=ParticipantListResponse, status_code=status.HTTP_200_OK)
async def list_participants_route(
    org_id: str, challenge_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/participants"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        check_challenge_visibility(current_user=current_user, challenge=challenge)
        rows, total = await get_participants(session=db, organization_id=org_id, challenge_id=challenge_id, offset=offset, limit=limit)
        items = [
            ParticipantInfo(
                user_id=u.id, first_name=u.first_name, last_name=u.last_name,
                rank=p.rank, completed_at=p.completed_at, joined_at=p.joined_at,
            )
            for p, u in rows
        ]
        return ParticipantListResponse(items=items, total=total, offset=offset, limit=limit)


@router.post(
    "/{challenge_id}/participants/{user_id}/complete",
    response_model=ParticipantInfo, status_code=status.HTTP_200_OK,
    description="Manual admin override — see TODO(background-job) for the intended automated path.",
)
async def mark_participant_complete_route(
    org_id: str, challenge_id: str, user_id: str, body: ParticipantCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.update")),
) -> ParticipantInfo:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/participants/{user_id}/complete"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)
        participant = await mark_participant_complete(
            session=db, organization_id=org_id, challenge_id=challenge_id, user_id=user_id, rank=body.rank,
        )
        await db.commit()

        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one()
        return ParticipantInfo(
            user_id=user.id, first_name=user.first_name, last_name=user.last_name,
            rank=participant.rank, completed_at=participant.completed_at, joined_at=participant.joined_at,
        )