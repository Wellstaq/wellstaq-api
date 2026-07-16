"""
app/services/post/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.models import Branch, Organization, Post, Permission, UserPermission


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


async def get_viewable_branch_ids(
    *, db: AsyncSession, current_user: CurrentUser, permission: str
) -> list[str] | None:
    """
    Returns the list of branch_ids current_user holds `permission` for,
    or None if they hold it org-wide (branch_id IS NULL) — None is the
    "sees everything" signal, distinct from an empty list ("holds it
    nowhere"). super_admin returns None.

    Needed because a user can hold the same permission scoped to
    MULTIPLE branches independently (e.g. manager of two branches) — a
    single require_scoped_permission check only answers about ONE branch
    at a time.
    """
    if current_user.role == "super_admin":
        return None

    query = (
        select(UserPermission.branch_id)
        .join(Permission, Permission.id == UserPermission.permission_id)
        .where(
            UserPermission.user_id == current_user.user_id,
            UserPermission.organization_id == current_user.organization_id,
            Permission.name == permission,
        )
    )
    result = await db.execute(query)
    branch_ids = [row[0] for row in result.all()]

    if None in branch_ids:  # org-wide grant present — inherits everywhere
        return None

    return branch_ids


def check_branch_list_access(
    *, current_user: CurrentUser, branch_id: str | None, viewable_branch_ids: list[str] | None
) -> None:
    """
    Gate for the explicit ?branch_id= query filter on list_posts_route.
    viewable_branch_ids comes from get_viewable_branch_ids — the full set
    of branches the caller holds post.view_all for (or None = sees
    everything, org-wide grant / super_admin).
    """
    if branch_id is None:
        return
    if current_user.branch_id == branch_id:
        return
    if viewable_branch_ids is None:
        return
    if branch_id in viewable_branch_ids:
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN, "You do not have access to this branch's posts."
    )


async def check_post_branch_access(
    *, db: AsyncSession, current_user: CurrentUser, post: Post
) -> None:
    """
    Visibility gate for a single post (get/like/comment/share/save).

    - post.branch_id is None (org-level post): visible to everyone in the org.
    - post.branch_id is set: visible to members of that branch, or anyone
      holding post.view_all scoped to that SPECIFIC branch (or org-wide) —
      checked via require_scoped_permission so a grant for branch A
      doesn't leak visibility into branch B. super_admin bypass is
      handled inside require_scoped_permission itself.

    NOTE: this is async — it requires a DB lookup for the scoped
    permission check. Every call site must await it and pass db=.
    """
    if post.branch_id is None:
        return
    if current_user.branch_id == post.branch_id:
        return

    await require_scoped_permission(
        db=db, current_user=current_user, permission="post.view_all", target_branch_id=post.branch_id,
    )