"""
app/services/chat/get_messages.py
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.models import GroupChatMessage
from app.services.chat._helpers import require_conversation_membership, validate_conversation


async def get_messages(
    *,
    session: AsyncSession,
    organization_id: str,
    conversation_type: str,
    conversation_id: str,
    current_user: CurrentUser,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[GroupChatMessage], int]:
    await validate_conversation(
        session=session, organization_id=organization_id,
        conversation_type=conversation_type, conversation_id=conversation_id,
    )
    await require_conversation_membership(
        session=session, current_user=current_user,
        conversation_type=conversation_type, conversation_id=conversation_id,
    )

    query = (
        select(GroupChatMessage)
        .where(
            GroupChatMessage.organization_id == organization_id,
            GroupChatMessage.conversation_type == conversation_type,
            GroupChatMessage.conversation_id == conversation_id,
        )
        .order_by(GroupChatMessage.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(GroupChatMessage).where(
        GroupChatMessage.organization_id == organization_id,
        GroupChatMessage.conversation_type == conversation_type,
        GroupChatMessage.conversation_id == conversation_id,
    )

    total = (await session.execute(count_query)).scalar_one()
    messages = (await session.execute(query)).scalars().all()
    return list(messages), total