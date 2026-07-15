"""
app/routers/chats.py

Group chat routes for clubs and events. conversation_type is 'club' or
'event' (see GroupChatMessage model). Pinning is restricted to
`chat.moderate` — an admin/manager permission, not structural.

TODO(membership): every route here checks org membership only, not
club/event membership specifically — see
app/services/chat/_helpers.py::validate_conversation.

Routes:
  POST   /organizations/{org_id}/conversations/{conversation_type}/{conversation_id}/messages
  GET    /organizations/{org_id}/conversations/{conversation_type}/{conversation_id}/messages
  POST   /organizations/{org_id}/conversations/messages/{message_id}/pin
  DELETE /organizations/{org_id}/conversations/messages/{message_id}/pin
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_permission, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.chat import (
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    PinActionResponse,
)
from app.services.chat import get_messages, pin_message, send_message, unpin_message

router = APIRouter(prefix="/organizations/{org_id}/conversations", tags=["chat"])

@router.post("/{conversation_type}/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message_route(
    org_id: str, conversation_type: str, conversation_id: str, body: MessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MessageResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/conversations/{conversation_type}/{conversation_id}/messages"):
        message = await send_message(
            session=db, organization_id=org_id, conversation_type=conversation_type, conversation_id=conversation_id,
            current_user=current_user, content=body.content, media_url=body.media_url, media_type=body.media_type,
        )
        await db.commit()
        return MessageResponse.model_validate(message)


@router.get("/{conversation_type}/{conversation_id}/messages", response_model=MessageListResponse, status_code=status.HTTP_200_OK)
async def list_messages_route(
    org_id: str, conversation_type: str, conversation_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MessageListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/conversations/{conversation_type}/{conversation_id}/messages"):
        messages, total = await get_messages(
            session=db, organization_id=org_id, conversation_type=conversation_type, conversation_id=conversation_id,
            current_user=current_user, offset=offset, limit=limit,
        )
        return MessageListResponse(
            items=[MessageResponse.model_validate(m) for m in messages], total=total, offset=offset, limit=limit,
        )
        
@router.post(
    "/messages/{message_id}/pin",
    response_model=PinActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Pin a message",
    description="Requires `chat.moderate`. Unpins any previously pinned message in the same conversation.",
)
async def pin_message_route(
    org_id: str, message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("chat.moderate")),
) -> PinActionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/conversations/messages/{message_id}/pin"):
        message = await pin_message(session=db, organization_id=org_id, message_id=message_id, pinned_by=current_user.user_id)
        await db.commit()
        return PinActionResponse(message_id=message.id, is_pinned=True, message="Pinned.")


@router.delete(
    "/messages/{message_id}/pin",
    response_model=PinActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Unpin a message",
    description="Requires `chat.moderate`.",
)
async def unpin_message_route(
    org_id: str, message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("chat.moderate")),
) -> PinActionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/conversations/messages/{message_id}/pin"):
        message = await unpin_message(session=db, organization_id=org_id, message_id=message_id)
        await db.commit()
        return PinActionResponse(message_id=message.id, is_pinned=False, message="Unpinned.")