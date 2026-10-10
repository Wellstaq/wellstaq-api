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

  
"""

from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import(
    CurrentUser,
    get_current_user,
    require_permission,
    require_same_organization,
    require_active_subscription,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import User
from app.schemas.challenge import (
    ChallengeCreateRequest,
    ChallengeListResponse,
    ChallengeResponse,
    ChallengeUpdateRequest,
    LeaderboardResponse,
    ParticipantCompleteRequest,
    ParticipantInfo,
    ParticipantJoinResponse,
    ParticipantListResponse,
    ParticipantScoreRequest,
    ProgressEntryResponse,
    ProgressSubmitRequest,
    ParticipantProgressResponse,
    ChallengeStatsResponse,
    SubmissionCreateRequest,
    SubmissionListResponse,
    SubmissionResponse,
    SubmissionReviewRequest,
    SubmissionReviewerInfo,
)
from app.services.challenge import (
    create_challenge,
    delete_challenge,
    edit_challenge,
    cancel_challenge,
    get_challenge,
    get_challenges,
    get_leaderboard,
    get_participants,
    join_challenge,
    leave_challenge,
    list_submissions,
    mark_participant_complete,
    review_submission,
    set_participant_score,
    submit_evidence,

)
from app.services.challenge._helpers import check_challenge_scope, check_challenge_visibility
from app.services.challenge.progress_service import submit_progress
from app.services.challenge.stats_service import get_challenge_stats
from datetime import date as date_type

from app.core.constants import CHALLENGE_STATUSES, SUBMISSION_REVIEW_STATUSES

router = APIRouter(prefix="/organizations/{org_id}/challenges", tags=["challenges"])


# ── Create ───────────────────────────────────────────────────────────────────
@router.post(
    "",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a challenge",
    description=(
        "Creates an org-level or branch-scoped wellness/fitness challenge. "
        "Requires `challenge.create`, scoped to `branch_id` (or org-wide "
        "if `branch_id` is omitted).\n\n"
        "**Required fields:** `name`, `target_type`.\n\n"
        "**branch_id** — omit for an org-wide challenge visible to every "
        "branch; set to scope the challenge to one branch only.\n\n"
        "**target_type** — determines how completion is evaluated:\n"
        "- `cumulative`: total value across the whole challenge window must "
        "reach `target_value` (e.g. 50,000 steps in total). Completes the "
        "moment the target is crossed, any time before `end_date`.\n"
        "- `daily_minimum`: `target_value` must be independently met "
        "**every day** within the window (e.g. 5,000 steps every day). "
        "Requires `end_date` to be set — completion is only finalized once "
        "the window closes.\n\n"
        "**target_value** — the numeric goal `target_type` is evaluated "
        "against. Optional; if omitted, the challenge has no automatic "
        "completion and must be marked complete manually.\n\n"
        "**metric_type** — freeform label describing what's being "
        "measured, e.g. `steps`, `minutes_active`, `workshop_attendance`, "
        "`water_intake_liters`. Not a fixed enum — pick a consistent label "
        "per challenge type, since the frontend/mobile client uses this to "
        "decide how to collect and submit progress.\n\n"
        "**status** is not settable here — every new challenge starts as "
        "`upcoming` and is activated by the lifecycle worker when its start date arrives.\n\n"
        "**wellbeing_challenge_id** — optional link to a global challenge "
        "category/theme (e.g. \"Fitness\", \"Mental Health\") for grouping "
        "and filtering; leave null for an uncategorized challenge."
    ),
)
async def create_challenge_route(
    org_id: str, body: ChallengeCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("challenge.create")),
) -> ChallengeResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges"):
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.create", branch_id=body.branch_id)
        challenge = await create_challenge(
            session=db, organization_id=org_id, branch_id=body.branch_id,
            wellbeing_challenge_id=body.wellbeing_challenge_id, name=body.name, description=body.description,
            image_url=body.image_url,  # new
            start_date=body.start_date, end_date=body.end_date, metric_type=body.metric_type,
            target_type=body.target_type, target_value=body.target_value,
            created_by=current_user.user_id,
            tracking_method=body.tracking_method, activity_label=body.activity_label,
            evidence_required=body.evidence_required, requires_approval=body.requires_approval,
        )
        await db.commit()
        return ChallengeResponse.model_validate(challenge)


@router.get("/stats", response_model=ChallengeStatsResponse, status_code=status.HTTP_200_OK)
async def get_challenge_stats_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    scope: Literal["organization", "all"] | None = Query(default=None),
    period: Literal["week", "month", "six_months", "custom"] | None = Query(
        default=None,
        description="Omit for all-time stats (default, no trend). Set to compare against a baseline for trend %.",
    ),
    start_date: date_type | None = Query(default=None, description="Required when period='custom'."),
    end_date: date_type | None = Query(default=None, description="Optional, defaults to today when period='custom'."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ChallengeStatsResponse:
    require_same_organization(org_id, current_user)
    if branch_id is not None and current_user.role != "super_admin" and current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch's challenges.")
    if period == "custom" and start_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date is required when period='custom'.")

    async with request_guard(db, "/organizations/{org_id}/challenges/stats"):
        stats = await get_challenge_stats(
            session=db, organization_id=org_id, branch_id=branch_id, scope=scope,
            viewer_branch_id=current_user.branch_id, is_super_admin=(current_user.role == "super_admin"),
            period=period, start_date=start_date, end_date=end_date,
        )
        return ChallengeStatsResponse(**stats)
# ── List ─────────────────────────────────────────────────────────────────────



ChallengeStatus = Literal[*CHALLENGE_STATUSES]

@router.get(
    "",
    response_model=ChallengeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List challenges",
    description=(
        "List challenges for an organization.\n\n"
        "- If `branch_id` is omitted and `scope` is omitted: default visibility "
        "rules apply (super_admin sees everything; other roles see their own "
        "branch's challenges plus org-wide ones).\n"
        "- `scope=organization`: only org-wide challenges (`branch_id` is null). "
        "If `branch_id` is also passed, returns that branch's challenges "
        "PLUS the org-wide ones.\n"
        "- `scope=all`: every challenge in the organization, ignoring branch "
        "boundaries entirely. Ignored if `branch_id` is passed.\n"
        "- `status`: filter by challenge status. If omitted, challenges of "
        "all statuses are returned."
    ),
)
async def list_challenges_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    scope: Literal["organization", "all"] | None = Query(
        default=None,
        description=(
            "'organization' = org-wide challenges only (branch_id is null); "
            "combined with branch_id, also includes that branch's challenges. "
            "'all' = every challenge in the org, ignoring branch boundaries "
            "(ignored if branch_id is set)."
        ),
    ),
    status_filter: ChallengeStatus | None = Query(
        default=None,
        alias="status",
        description=f"Filter challenges by status: {', '.join(CHALLENGE_STATUSES)}. Omit to fetch all statuses.",
    ),
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
            session=db, organization_id=org_id, branch_id=branch_id, scope=scope, status=status_filter,
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
     current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("challenge.update")),
) -> ChallengeResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)
        updated = await edit_challenge(
            session=db, organization_id=org_id, challenge_id=challenge_id,
            name=body.name, description=body.description, image_url=body.image_url,  # new
            start_date=body.start_date, end_date=body.end_date,
            metric_type=body.metric_type, target_type=body.target_type, target_value=body.target_value,
            status_=body.status,
            tracking_method=body.tracking_method, activity_label=body.activity_label,
            evidence_required=body.evidence_required, requires_approval=body.requires_approval,
        )
        await db.commit()
        return ChallengeResponse.model_validate(updated)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete(
    "/{challenge_id}",
    status_code=status.HTTP_200_OK,
    summary="Permanently delete a challenge",
    description=(
        "Hard delete — also removes all participant and progress records. "
        "Refuses with 409 if any participant has already completed the "
        "challenge; use `/cancel` instead in that case. Requires `challenge.delete`."
    ),
)
async def delete_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
     current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("challenge.delete")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.delete", branch_id=challenge.branch_id)
        await delete_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await db.commit()
        return {"success": True}
        


# ── Join / Leave ─────────────────────────────────────────────────────────────

@router.post("/{challenge_id}/join", response_model=ParticipantJoinResponse, status_code=status.HTTP_201_CREATED)
async def join_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
     current_user: CurrentUser = Depends(require_active_subscription),
) -> ParticipantJoinResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/join"):
        await join_challenge(session=db, organization_id=org_id, challenge_id=challenge_id, current_user=current_user)
        await db.commit()
        return ParticipantJoinResponse(challenge_id=challenge_id, user_id=current_user.user_id, message="Joined challenge.")

@router.delete("/{challenge_id}/join", status_code=status.HTTP_200_OK)
async def leave_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
) -> dict:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/join"):
        await leave_challenge(session=db, organization_id=org_id, challenge_id=challenge_id, current_user=current_user)
        await db.commit()
        return {"success": True}

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
    current_user: CurrentUser = Depends(require_active_subscription),
   
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
        return ProgressEntryResponse(
            entry_date=entry.entry_date, value=entry.value, source=entry.source,
            already_completed=False, message="Progress recorded.",
        )

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
                rank=p.rank, score=p.score, completed_at=p.completed_at, joined_at=p.joined_at,
            )
            for p, u in rows
        ]
        return ParticipantListResponse(items=items, total=total, offset=offset, limit=limit)


@router.post(
    "/{challenge_id}/participants/{user_id}/complete",
    response_model=ParticipantInfo, status_code=status.HTTP_200_OK,
    description="Manual admin override ",
)
async def mark_participant_complete_route(
    org_id: str, challenge_id: str, user_id: str, body: ParticipantCompleteRequest,
    db: AsyncSession = Depends(get_db),
     current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("challenge.update")),
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
            rank=participant.rank, score=participant.score,
            completed_at=participant.completed_at, joined_at=participant.joined_at,
        )

@router.post(
    "/{challenge_id}/cancel",
    response_model=ChallengeResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel a challenge",
    description=(
        "Sets status='cancelled'. Preferred over DELETE when the "
        "challenge has completions or active participants worth "
        "keeping — preserves all participant and progress history. "
        "Requires `challenge.update`."
    ),
)
async def cancel_challenge_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("challenge.update")),
) -> ChallengeResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/cancel"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)
        cancelled = await cancel_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await db.commit()
        return ChallengeResponse.model_validate(cancelled)

@router.get(
    "/{challenge_id}/progress/me",
    response_model=ParticipantProgressResponse,
    status_code=status.HTTP_200_OK,
    summary="My progress in this challenge",
    description=(
        "Shows current standing: total logged so far, percent complete "
        "(cumulative challenges only — daily_minimum returns null, see "
        "service docstring), and days remaining."
    ),
)
async def my_progress_route(
    org_id: str, challenge_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantProgressResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/progress/me"):
        from app.services.challenge.progress_service import get_participant_progress
        progress = await get_participant_progress(
            session=db, organization_id=org_id, challenge_id=challenge_id, user_id=current_user.user_id,
        )
        return ParticipantProgressResponse(**progress)


@router.get(
    "/{challenge_id}/progress/{user_id}",
    response_model=ParticipantProgressResponse,
    status_code=status.HTTP_200_OK,
    description="View a specific participant's progress. Requires `challenge.update` scoped to the challenge's branch.",
)
async def participant_progress_route(
    org_id: str, challenge_id: str, user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.update")),
) -> ParticipantProgressResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/progress/{user_id}"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)

        from app.services.challenge.progress_service import get_participant_progress
        progress = await get_participant_progress(
            session=db, organization_id=org_id, challenge_id=challenge_id, user_id=user_id,
        )
        return ParticipantProgressResponse(**progress)


# ── Evidence submissions ─────────────────────────────────────────────────────

@router.post(
    "/{challenge_id}/submissions",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit evidence for today",
    description=(
        "Evidence-based challenges only (`tracking_method='evidence_based'`). "
        "Counts toward progress immediately if the challenge's "
        "`requires_approval` is False; otherwise sits `pending` until an "
        "admin reviews it via the submissions review endpoints."
    ),
)
async def submit_evidence_route(
    org_id: str, challenge_id: str, body: SubmissionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
) -> SubmissionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/submissions"):
        entry = await submit_evidence(
            session=db, organization_id=org_id, challenge_id=challenge_id,
            user_id=current_user.user_id, photo_url=body.photo_url, note=body.note,
        )
        await db.commit()
        return SubmissionResponse.model_validate(entry)


SubmissionReviewStatus = Literal[*SUBMISSION_REVIEW_STATUSES]

@router.get(
    "/{challenge_id}/submissions",
    response_model=SubmissionListResponse,
    status_code=status.HTTP_200_OK,
    summary="Review queue for evidence submissions",
    description="Admin review queue. Requires `challenge.update` scoped to the challenge's branch. Defaults to pending submissions only.",
)
async def list_submissions_route(
    org_id: str, challenge_id: str,
    review_status: SubmissionReviewStatus | None = Query(default="pending", alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.update")),
) -> SubmissionListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/submissions"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)

        rows, total = await list_submissions(
            session=db, organization_id=org_id, challenge_id=challenge_id,
            review_status=review_status, offset=offset, limit=limit,
        )
        items = [
            SubmissionReviewerInfo(
                id=entry.id, user_id=user.id, first_name=user.first_name, last_name=user.last_name,
                entry_date=entry.entry_date, photo_url=entry.photo_url, note=entry.note,
                review_status=entry.review_status, rejection_reason=entry.rejection_reason,
            )
            for entry, user in rows
        ]
        return SubmissionListResponse(items=items, total=total, offset=offset, limit=limit)


@router.post(
    "/{challenge_id}/submissions/{entry_id}/review",
    response_model=SubmissionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve or reject an evidence submission",
    description=(
        "Requires `challenge.update` scoped to the challenge's branch. "
        "Approving counts the entry toward progress (and may trigger "
        "challenge completion); rejecting requires a `reason`, which the "
        "participant is notified with."
    ),
)
async def review_submission_route(
    org_id: str, challenge_id: str, entry_id: str, body: SubmissionReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.update")),
) -> SubmissionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/submissions/{entry_id}/review"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)

        entry = await review_submission(
            session=db, organization_id=org_id, challenge_id=challenge_id, entry_id=entry_id,
            reviewer_id=current_user.user_id, action=body.action, reason=body.reason,
        )
        await db.commit()
        return SubmissionResponse.model_validate(entry)


# ── Scoring & leaderboard ─────────────────────────────────────────────────────

@router.patch(
    "/{challenge_id}/participants/{user_id}/score",
    response_model=ParticipantInfo,
    status_code=status.HTTP_200_OK,
    description=(
        "Admin-assigned quality score (0-100) for competition-style "
        "challenges judged on submission quality, not just completion "
        "count. Requires `challenge.update` scoped to the challenge's branch."
    ),
)
async def set_participant_score_route(
    org_id: str, challenge_id: str, user_id: str, body: ParticipantScoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("challenge.update")),
) -> ParticipantInfo:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/participants/{user_id}/score"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        await check_challenge_scope(db=db, current_user=current_user, permission="challenge.update", branch_id=challenge.branch_id)

        participant = await set_participant_score(
            session=db, organization_id=org_id, challenge_id=challenge_id, user_id=user_id, score=body.score,
        )
        await db.commit()

        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one()
        return ParticipantInfo(
            user_id=user.id, first_name=user.first_name, last_name=user.last_name,
            rank=participant.rank, score=participant.score,
            completed_at=participant.completed_at, joined_at=participant.joined_at,
        )


@router.get(
    "/{challenge_id}/leaderboard",
    response_model=LeaderboardResponse,
    status_code=status.HTTP_200_OK,
    description=(
        "Ranked by admin-assigned score where set, otherwise by approved "
        "progress value, then by who completed first."
    ),
)
async def leaderboard_route(
    org_id: str, challenge_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LeaderboardResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/challenges/{challenge_id}/leaderboard"):
        challenge = await get_challenge(session=db, organization_id=org_id, challenge_id=challenge_id)
        check_challenge_visibility(current_user=current_user, challenge=challenge)

        items, total = await get_leaderboard(
            session=db, organization_id=org_id, challenge_id=challenge_id, offset=offset, limit=limit,
        )
        return LeaderboardResponse(items=items, total=total, offset=offset, limit=limit)
