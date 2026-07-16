"""
app/services/chat/pin_service.py

Only one pinned message per conversation — enforced by the DB's partial
unique index. Pinning a new message while one is already pinned must
unpin the old one first, in the same transaction, or the insert/update
will violate the unique index.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.models import GroupChatMessage
from app.services.chat._helpers import get_message_or_404, require_chat_moderation_access


async def pin_message(
    *, session: AsyncSession, organization_id: str, message_id: str, current_user: CurrentUser
) -> GroupChatMessage:
    message = await get_message_or_404(session=session, organization_id=organization_id, message_id=message_id)
    await require_chat_moderation_access(session=session, current_user=current_user, message=message)

    existing_pinned = await session.execute(
        select(GroupChatMessage).where(
            GroupChatMessage.conversation_type == message.conversation_type,
            GroupChatMessage.conversation_id == message.conversation_id,
            GroupChatMessage.is_pinned.is_(True),
        )
    )
    for other in existing_pinned.scalars().all():
        other.is_pinned = False
        other.pinned_by = None
        other.pinned_at = None
    await session.flush()

    message.is_pinned = True
    message.pinned_by = current_user.user_id
    message.pinned_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(message)
    return message


async def unpin_message(
    *, session: AsyncSession, organization_id: str, message_id: str, current_user: CurrentUser
) -> GroupChatMessage:
    message = await get_message_or_404(session=session, organization_id=organization_id, message_id=message_id)
    await require_chat_moderation_access(session=session, current_user=current_user, message=message)

    message.is_pinned = False
    message.pinned_by = None
    message.pinned_at = None
    await session.flush()
    await session.refresh(message)
    return message