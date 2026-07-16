"""
app/services/post/share_service.py

No toggle/dedup on the share event itself — every call is a new
PostShare row (see model docstring). destination_type/id are optional:
omitted = signal-only share; set = also drops a GroupChatMessage into
that conversation referencing the post.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.auth.dependencies import CurrentUser
from app.models import PostShare
from app.services.chat._helpers import validate_conversation
from app.services.chat.send_message import send_message
from app.services.post._helpers import check_post_branch_access, get_post_or_404
from sqlalchemy.ext.asyncio import AsyncSession


async def share_post(
    *,
    session: AsyncSession,
    organization_id: str,
    post_id: str,
    current_user: CurrentUser,
    destination_type: str | None = None,
    destination_id: str | None = None,
) -> PostShare:
    if (destination_type is None) != (destination_id is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "destination_type and destination_id must both be set, or both omitted."
        )

    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    await check_post_branch_access(db=session, current_user=current_user, post=post)

    group_chat_message_id = None
    if destination_type is not None:
        # validate_conversation confirms the club/event exists in this org
        # AND that conversation_type is one of the allowed values —
        # send_message below also runs its own membership check
        # (club/event participation), so a user can only share into a
        # conversation they actually belong to.
        await validate_conversation(
            session=session, organization_id=organization_id,
            conversation_type=destination_type, conversation_id=destination_id,
        )
        preview = (post.content or "").strip()[:200]
        message_content = f"Shared a post: {preview}" if preview else "Shared a post."
        message = await send_message(
            session=session, organization_id=organization_id,
            conversation_type=destination_type, conversation_id=destination_id,
            current_user=current_user, content=message_content,
            media_url=post.media_url, media_type=post.media_type,
        )
        group_chat_message_id = message.id

    share = PostShare(
        organization_id=organization_id,
        post_id=post_id,
        user_id=current_user.user_id,
        destination_type=destination_type,
        destination_id=destination_id,
        group_chat_message_id=group_chat_message_id,
    )
    session.add(share)
    post.share_count += 1
    await session.flush()

    return share