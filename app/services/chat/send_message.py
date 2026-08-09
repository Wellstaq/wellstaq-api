"""
app/services/chat/send_message.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.models import GroupChatMessage
from app.services.chat._helpers import (
    require_conversation_membership,
    validate_conversation,
)
from app.services.storage import attach_media_asset, confirm_upload
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def send_message(
    *,
    session: AsyncSession,
    organization_id: str,
    conversation_type: str,
    conversation_id: str,
    current_user: CurrentUser,
    content: str | None,
    media_url: str | None,
    media_type: str | None,
) -> GroupChatMessage:
    # Normalize content first
    if content is not None:
        content = content.strip()
        if content == "":
            content = None

    # Validate payload before any database lookups
    if content is None and media_url is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A message needs content or media.",
        )

    object_key = None
    if media_url is not None:
        object_key = extract_object_key(media_url)
        if not confirm_upload(object_key=object_key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")

    await validate_conversation(
        session=session,
        organization_id=organization_id,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
    )

    await require_conversation_membership(
        session=session,
        current_user=current_user,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
    )

    message = GroupChatMessage(
        organization_id=organization_id,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        user_id=current_user.user_id,
        content=content,
        media_url=media_url,
        media_type=media_type,
        is_pinned=False,
    )

    session.add(message)
    await session.flush()
    await session.refresh(message)

    # Mark the uploaded media (if any) as attached so the pending-asset
    # sweep job leaves it alone. No-ops cleanly if object_key is None.
    await attach_media_asset(
        session=session, object_key=object_key,
        attached_to_type="chat_message", attached_to_id=message.id,
    )

    # TODO(redis/background-job): fan out a real-time notification/websocket
    # push to conversation members here once that infra exists.

    logger.info(
        "Group chat message sent "
        "message_id=%s conversation_type=%s conversation_id=%s user_id=%s",
        message.id,
        message.conversation_type,
        message.conversation_id,
        current_user.user_id,
    )

    return message