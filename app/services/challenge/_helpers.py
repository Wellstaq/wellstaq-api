"""
app/services/challenge/_helpers.py
"""

from __future__ import annotations

import logging


from urllib.parse import urlparse

from sqlalchemy.sql import Select
from fastapi import HTTPException, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.storage import confirm_upload
from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.models import Branch, Challenge, Organization, ChallengeProgressEntry

logger = logging.getLogger(__name__)

async def get_challenge_or_404(*, session: AsyncSession, organization_id: str, challenge_id: str) -> Challenge:
    result = await session.execute(
        select(Challenge).where(Challenge.id == challenge_id, Challenge.organization_id == organization_id)
    )
    challenge = result.scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found.")
    return challenge


async def validate_org_and_branch(
    *, session: AsyncSession, organization_id: str, branch_id: str | None
) -> None:
    org_result = await session.execute(select(Organization).where(Organization.id == organization_id))
    if org_result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")

    if branch_id is not None:
        branch_result = await session.execute(
            select(Branch).where(Branch.id == branch_id, Branch.organization_id == organization_id)
        )
        if branch_result.scalar_one_or_none() is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Branch not found or does not belong to this organization."
            )


async def check_challenge_scope(
    *, db: AsyncSession, current_user: CurrentUser, permission: str, branch_id: str | None
) -> None:
    """create/update/delete — always requires the permission, scoped to
    branch_id (None = org-wide grant required), same pattern as events."""
    await require_scoped_permission(
        db=db, current_user=current_user, permission=permission, target_branch_id=branch_id,
    )


def check_challenge_visibility(*, current_user: CurrentUser, challenge: Challenge) -> None:
    """
    No challenge.view_all permission exists in the current seed — a user
    sees org-wide challenges (branch_id=None) plus their own branch's
    challenges only. If cross-branch visibility for managers is needed
    later, add a challenge.view_all permission to the seed and mirror
    check_post_branch_access's pattern here rather than inventing scope
    that isn't backed by a real permission grant.
    """
    if challenge.branch_id is None:
        return
    if current_user.role == "super_admin":
        return
    if current_user.branch_id != challenge.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You do not have access to this branch's challenges."
        )


def check_participation_eligibility(*, current_user: CurrentUser, challenge: Challenge) -> None:
    """Self-service join/leave — same branch-membership rule as
    visibility. A user can only join a challenge they can see."""
    check_challenge_visibility(current_user=current_user, challenge=challenge)


 
async def check_challenge_completion(*, session: AsyncSession, challenge, participant) -> None:
    """
    cumulative: sum ALL entries so far, complete the instant it crosses
    target_value.

    daily_minimum: checks TODAY's entry specifically and fires
    notify_daily_goal_met (deduped to once/day) if today's value meets
    target_value. This is separate from the FINAL completion
    notification, which only fires once, on the day the whole window
    closes, and only for participants who passed every single day — see
    _finalize_daily_minimum_challenge in
    app/jobs/challenges/lifecycle.py.
    """
    if challenge.target_value is None:
        return

    if challenge.target_type == "cumulative":
        total_result = await session.execute(
            select(func.sum(ChallengeProgressEntry.value)).where(
                ChallengeProgressEntry.challenge_id == challenge.id,
                ChallengeProgressEntry.user_id == participant.user_id,
            )
        )
        total = total_result.scalar_one() or 0
        if total >= challenge.target_value:
            await mark_challenge_complete(session=session, challenge=challenge, participant=participant)

    elif challenge.target_type == "daily_minimum":
        from app.services.activity_log._helpers import today_utc

        today = today_utc()
        today_entry_result = await session.execute(
            select(ChallengeProgressEntry.value).where(
                ChallengeProgressEntry.challenge_id == challenge.id,
                ChallengeProgressEntry.user_id == participant.user_id,
                ChallengeProgressEntry.entry_date == today,
            )
        )
        today_value = today_entry_result.scalar_one_or_none()
        if today_value is not None and today_value >= challenge.target_value:
            await notify_daily_goal_met(session=session, challenge=challenge, participant=participant, entry_date=today)

async def mark_challenge_complete(*, session: AsyncSession, challenge, participant) -> None:
    """Sets completed_at and fires the challenge_completed notification.
    Called from check_challenge_completion (cumulative, inline) and from
    the daily_minimum finalization job (app/jobs/challenges/lifecycle.py)."""
    
    from datetime import datetime, timezone
    from app.services.notification import create_notification

    participant.completed_at = datetime.now(timezone.utc)
    await session.flush()

    await create_notification(
        session=session, user_id=participant.user_id, organization_id=challenge.organization_id,
        notification_type="challenge_completed", reference_id=challenge.id, reference_type="challenge",
        body=f"You completed the '{challenge.name}' challenge!",
    )
    logger.info("Challenge auto-completed", extra={"challenge_id": challenge.id, "user_id": participant.user_id})
    
async def notify_daily_goal_met(*, session: AsyncSession, challenge, participant, entry_date) -> None:
    """
    Fires when a single day's entry meets target_value for a
    daily_minimum challenge — distinct from mark_challenge_complete,
    which only fires once at the very end of the whole window.

    Deduped WITHOUT a separate tracking table: checks whether a
    'challenge_daily_goal_met' Notification already exists for this
    user + this challenge, created TODAY, before creating a new one.
    This relies on Notification.created_at, not entry_date, as the
    dedup key — correct because this function is only ever called on
    the same day the entry was submitted (never backfilled), so "today"
    and "entry_date" are always the same day in practice.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models import Notification
    from app.services.notification import create_notification

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    existing = await session.execute(
        select(Notification.id).where(
            Notification.user_id == participant.user_id,
            Notification.reference_id == challenge.id,
            Notification.notification_type == "challenge_daily_goal_met",
            Notification.created_at >= today_start,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return  # already notified today for this challenge — skip

    await create_notification(
        session=session, user_id=participant.user_id, organization_id=challenge.organization_id,
        notification_type="challenge_daily_goal_met",
        reference_id=challenge.id, reference_type="challenge",
        body=f"You hit today's goal in the '{challenge.name}' challenge! Keep it up.",
    )


def apply_challenge_visibility_filter(
    query: Select, *, branch_id: str | None, scope: str | None,
    viewer_branch_id: str | None, is_super_admin: bool,
) -> Select:
    """
    Which CHALLENGES are visible/countable — same rule used for both the
    list endpoint and stats. Does NOT filter participants.
    """
    if branch_id is not None:
        if scope == "organization":
            return query.where(or_(Challenge.branch_id == branch_id, Challenge.branch_id.is_(None)))
        return query.where(Challenge.branch_id == branch_id)
    if scope == "organization":
        return query.where(Challenge.branch_id.is_(None))
    if scope == "all":
        return query
    if not is_super_admin:
        return query.where(or_(Challenge.branch_id.is_(None), Challenge.branch_id == viewer_branch_id))
    return query



def confirm_challenge_image(image_url: str | None) -> None:
    """Same pattern as avatar_service.update_avatar — derive object_key
    from the URL path and verify it actually exists in S3 before trusting
    it, rather than accepting whatever URL the client sends."""
    if not image_url:
        return
    object_key = urlparse(image_url).path.lstrip("/")
    if not confirm_upload(object_key=object_key):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Challenge image upload not found — upload may have failed or not completed yet.",
        )