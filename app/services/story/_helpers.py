"""
app/services/story/_helpers.py

Deliberately mirrors app/services/post/_helpers.py's branch-access and
creation-membership patterns — Story has the same branch_id-nullable
shape as Post (org-level vs branch-level), so the same rules apply:
open self-service creation restricted to your own branch (or org-wide),
and visibility gated the same way.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.models import Branch, Organization, Story




async def get_story_or_404(*, session: AsyncSession, organization_id: str, story_id: str) -> Story:
    result = await session.execute(
        select(Story).where(Story.id == story_id, Story.organization_id == organization_id)
    )
    story = result.scalar_one_or_none()
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Story not found.")
    return story


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


def check_story_creation_membership(*, current_user: CurrentUser, branch_id: str | None) -> None:
    """No permission required — same open self-service rule as posts.
    branch_id=None (org-level) is open to anyone; branch_id set requires
    membership in that branch (or super_admin)."""
    if branch_id is None:
        return
    if current_user.role == "super_admin":
        return
    if current_user.branch_id != branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only post stories to your own branch.")


async def check_story_branch_access(*, db: AsyncSession, current_user: CurrentUser, story: Story) -> None:
    """Same scoped-permission visibility rule as check_post_branch_access
    — org-level stories visible to everyone; branch-level stories
    visible to that branch's members, or anyone holding story.view_all
    (if such a permission is ever added) scoped to that branch."""
    if story.branch_id is None:
        return
    if current_user.branch_id == story.branch_id:
        return

    await require_scoped_permission(
        db=db, current_user=current_user, permission="story.view_all", target_branch_id=story.branch_id,
    )