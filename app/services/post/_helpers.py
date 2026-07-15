"""
app/services/post/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.models import Branch, Organization, Post


async def get_post_or_404(*, session: AsyncSession, organization_id: str, post_id: str) -> Post:
    result = await session.execute(
        select(Post).where(Post.id == post_id, Post.organization_id == organization_id)
    )
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found.")
    return post


async def validate_org_and_branch(
    *, session: AsyncSession, organization_id: str, branch_id: str | None
) -> None:
    """Raises 404 if org doesn't exist, or if branch_id is set but doesn't
    belong to this org. branch_id=None is valid (org-level post)."""
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


def check_post_creation_membership(*, current_user: CurrentUser, branch_id: str | None) -> None:
    """
    Creation-time check — no permission required, only membership.

    - branch_id=None (org-level post): open to any authenticated org
      member. No restriction.
    - branch_id=X (branch-scoped post): the poster must belong to that
      branch (current_user.branch_id == branch_id), or be super_admin.
      You can only post into your own branch's feed — not anyone else's.

    This intentionally does NOT reuse require_same_branch_or_org_level:
    that helper requires super_admin for target_branch_id=None, which
    would lock org-level posting down to admins. Here org-level posting is
    open to everyone, so the semantics diverge and a dedicated check is
    correct rather than a misuse of the existing one.
    """
    if branch_id is None:
        return

    if current_user.role == "super_admin":
        return

    if current_user.branch_id != branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only post to your own branch."
        )


async def check_post_scope(
    *, db: AsyncSession, current_user: CurrentUser, permission: str, branch_id: str | None
) -> None:
    """
    Scope check for update/delete/moderate — these remain privileged
    actions requiring an explicit permission grant, unlike creation.

    branch_id=None => org-wide grant of `permission` required.
    branch_id=X    => branch-scoped or org-wide grant required.
    """
    await require_scoped_permission(
        db=db, current_user=current_user, permission=permission, target_branch_id=branch_id,
    )


def check_post_branch_access(*, current_user: CurrentUser, post: Post) -> None:
    """
    Visibility gate for reactions (like/save/share) — separate from
    creation membership, but same underlying rule: you can only interact
    with posts in your own branch, or org-level posts (visible to all).

    - post.branch_id is None (org-level post): visible to everyone in the org.
    - post.branch_id is set: only members of that branch, or super_admin.
    """
    if post.branch_id is None:
        return

    if current_user.role == "super_admin":
        return

    if current_user.branch_id != post.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You do not have access to posts outside your branch."
        )