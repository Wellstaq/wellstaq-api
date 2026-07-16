"""
app/services/chat/_helpers.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    require_club_membership,
    require_event_participation,
    require_scoped_permission,
)
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

async def get_conversation_branch_id(
    *, session: AsyncSession, conversation_type: str, conversation_id: str
) -> str:
    """Returns the branch_id that owns this conversation — every club and
    event is branch-scoped, so this always resolves to a real branch_id,
    never None."""
    if conversation_type == "club":
        result = await session.execute(select(Club).where(Club.id == conversation_id))
    else:  # event
        result = await session.execute(select(Event).where(Event.id == conversation_id))

    resource = result.scalar_one_or_none()
    if resource is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{conversation_type.capitalize()} not found.")
    return resource.branch_id


async def require_chat_moderation_access(
    *, session: AsyncSession, current_user: CurrentUser, message: GroupChatMessage
) -> None:
    """
    Two independent paths grant moderation access to a message's
    conversation, checked in this order:

      1. Club leader override — if conversation_type == 'club' and
         current_user is that club's leader_id, they can moderate their
         own club's chat with NO permission grant required. This mirrors
         a post author deleting their own post: ownership, not a
         permission row. Does NOT apply to events (no equivalent
         "event owner" concept confirmed yet — event moderation goes
         through scoped chat.moderate only).

      2. Scoped chat.moderate permission — checked against the branch
         that owns the conversation (every club/event is branch-scoped,
         so this always has a real target_branch_id, never None). A
         branch-scoped OR org-wide grant satisfies this, same as every
         other require_scoped_permission call. super_admin bypasses
         inside require_scoped_permission itself.

    Raises:
      403 — neither path grants access.
    """
    if message.conversation_type == "club":
        club_result = await session.execute(select(Club).where(Club.id == message.conversation_id))
        club = club_result.scalar_one_or_none()
        if club is not None and club.leader_id == current_user.user_id:
            return  # club leader — no permission check needed

    branch_id = await get_conversation_branch_id(
        session=session, conversation_type=message.conversation_type, conversation_id=message.conversation_id
    )
    await require_scoped_permission(
        db=session, current_user=current_user, permission="chat.moderate", target_branch_id=branch_id,
    )