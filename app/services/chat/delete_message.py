"""
app/services/chat/delete_message.py

Deletion access — three independent paths, checked in order:

  1. Club leader override — if conversation_type == 'club' and the
     current_user is that club's leader_id, they can delete any message
     in their own club's chat. No permission grant needed.

  2. Event creator override — if conversation_type == 'event' and the
     current_user created that event (event.created_by == user_id),
     they can delete any message in that event's chat. No permission
     grant needed.

  3. Scoped chat.moderate permission — branch-scoped or org-wide grant.
     super_admin bypasses inside require_scoped_permission itself.

Raises 403 if none of the four paths grant access.
Raises 404 if the message doesn't exist in the org.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_scoped_permission
from app.models import Club, Event, GroupChatMessage
from app.services.chat._helpers import get_conversation_branch_id, get_message_or_404

logger = logging.getLogger(__name__)

async def _has_deletion_access(
    *,
    session: AsyncSession,
    current_user: CurrentUser,
    message: GroupChatMessage,
) -> bool:
    if message.conversation_type == "club":
        # Club leader
        result = await session.execute(
            select(Club).where(Club.id == message.conversation_id)
        )
        club = result.scalar_one_or_none()
        if club is not None and club.leader_id == current_user.user_id:
            return True

    elif message.conversation_type == "event":
        # Event creator
        result = await session.execute(
            select(Event).where(Event.id == message.conversation_id)
        )
        event = result.scalar_one_or_none()
        if event is not None and event.created_by == current_user.user_id:
            return True

    return False


    
async def delete_message(
    *,
    session: AsyncSession,
    organization_id: str,
    message_id: str,
    current_user: CurrentUser,
) -> None:
    message = await get_message_or_404(
        session=session, organization_id=organization_id, message_id=message_id
    )

    if not await _has_deletion_access(
        session=session, current_user=current_user, message=message
    ):
        # Path 4 — scoped chat.moderate. require_scoped_permission raises
        # 403 internally if the grant isn't found, so we don't need to
        # raise here ourselves — just let it propagate.
        branch_id = await get_conversation_branch_id(
            session=session,
            conversation_type=message.conversation_type,
            conversation_id=message.conversation_id,
        )
        await require_scoped_permission(
            db=session,
            current_user=current_user,
            permission="chat.moderate",
            target_branch_id=branch_id,
        )

    await session.delete(message)
    await session.flush()

    logger.info(
        "Group chat message deleted",
        extra={
            "message_id": message_id,
            "deleted_by": current_user.user_id,
            "conversation_type": message.conversation_type,
            "conversation_id": message.conversation_id,
        },
    )