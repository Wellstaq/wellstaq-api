"""
app/services/challenge/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.models import Branch, Challenge, Organization


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