"""
app/services/chat/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_club_membership, require_event_participation
from app.models import Club, Event, GroupChatMessage

VALID_CONVERSATION_TYPES = {"club", "event"}


async def validate_conversation(
    *, session: AsyncSession, organization_id: str, conversation_type: str, conversation_id: str
) -> None:
    """Confirms conversation_type is valid AND the target club/event
    exists and belongs to organization_id. Does NOT check membership —
    see require_conversation_membership for that."""
    if conversation_type not in VALID_CONVERSATION_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"conversation_type must be one of {sorted(VALID_CONVERSATION_TYPES)}.",
        )

    if conversation_type == "club":
        result = await session.execute(
            select(Club).where(Club.id == conversation_id, Club.organization_id == organization_id)
        )
    else:  # event
        result = await session.execute(
            select(Event).where(Event.id == conversation_id, Event.organization_id == organization_id)
        )

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{conversation_type.capitalize()} not found in this organization."
        )


async def require_conversation_membership(
    *, session: AsyncSession, current_user: CurrentUser, conversation_type: str, conversation_id: str
) -> None:
    """Dispatches to the matching membership check in dependencies.py
    based on conversation_type. Kept here (not inlined at every call
    site) so send_message/get_messages/share_post don't each need to
    know the club-vs-event branching themselves."""
    if conversation_type == "club":
        await require_club_membership(db=session, current_user=current_user, club_id=conversation_id)
    else:  # event
        await require_event_participation(db=session, current_user=current_user, event_id=conversation_id)


async def get_message_or_404(*, session: AsyncSession, organization_id: str, message_id: str) -> GroupChatMessage:
    result = await session.execute(
        select(GroupChatMessage).where(
            GroupChatMessage.id == message_id, GroupChatMessage.organization_id == organization_id
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found.")
    return message