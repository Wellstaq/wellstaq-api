"""
app/services/post/comment_service.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.models import PostComment
from app.services.post._helpers import check_post_branch_access, get_post_or_404

logger = logging.getLogger(__name__)


async def add_comment(
    *,
    session: AsyncSession,
    organization_id: str,
    post_id: str,
    current_user: CurrentUser,
    content: str,
    parent_comment_id: str | None,
) -> PostComment:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    check_post_branch_access(current_user=current_user, post=post)

    if parent_comment_id is not None:
        parent_result = await session.execute(
            select(PostComment).where(PostComment.id == parent_comment_id, PostComment.post_id == post_id)
        )
        if parent_result.scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent comment not found on this post.")

    comment = PostComment(
        organization_id=organization_id,
        post_id=post_id,
        user_id=current_user.user_id,
        content=content.strip(),
        parent_comment_id=parent_comment_id,
    )
    session.add(comment)
    post.comment_count += 1
    await session.flush()
    await session.refresh(comment)

    return comment


async def edit_comment(
    *, session: AsyncSession, organization_id: str, post_id: str, comment_id: str, content: str
) -> PostComment:
    result = await session.execute(
        select(PostComment).where(
            PostComment.id == comment_id, PostComment.post_id == post_id, PostComment.organization_id == organization_id
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found.")

    comment.content = content.strip()
    await session.flush()
    await session.refresh(comment)
    return comment


async def delete_comment(*, session: AsyncSession, organization_id: str, post_id: str, comment_id: str) -> None:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)

    result = await session.execute(
        select(PostComment).where(
            PostComment.id == comment_id, PostComment.post_id == post_id, PostComment.organization_id == organization_id
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found.")

    reply_count_result = await session.execute(
        select(func.count()).select_from(PostComment).where(PostComment.parent_comment_id == comment_id)
    )
    reply_count = reply_count_result.scalar_one()

    await session.delete(comment)
    post.comment_count = max(0, post.comment_count - 1 - reply_count)
    await session.flush()


async def get_comments(
    *, session: AsyncSession, organization_id: str, post_id: str, offset: int = 0, limit: int = 50
) -> tuple[list[PostComment], int]:
    await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)

    query = (
        select(PostComment)
        .where(PostComment.post_id == post_id, PostComment.parent_comment_id.is_(None))
        .order_by(PostComment.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(PostComment).where(
        PostComment.post_id == post_id, PostComment.parent_comment_id.is_(None)
    )

    total = (await session.execute(count_query)).scalar_one()
    comments = (await session.execute(query)).scalars().all()
    return list(comments), total