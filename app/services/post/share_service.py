"""
app/services/post/share_service.py

No toggle/dedup — every call is a new event row (see model docstring).
"""

from __future__ import annotations

from app.auth.dependencies import CurrentUser
from app.models import PostShare
from app.services.post._helpers import check_post_branch_access, get_post_or_404
from sqlalchemy.ext.asyncio import AsyncSession


async def share_post(
    *, session: AsyncSession, organization_id: str, post_id: str, current_user: CurrentUser, destination: str | None = None
) -> PostShare:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    check_post_branch_access(current_user=current_user, post=post)

    share = PostShare(organization_id=organization_id, post_id=post_id, user_id=current_user.user_id)
    session.add(share)
    await session.flush()

    # TODO(redis): share events are a good trending signal — bump a
    # weighted counter here if/when you build a trending feed.

    return share