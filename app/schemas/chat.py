"""
app/schemas/chat.py
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreateRequest(BaseModel):
    content: str | None = Field(default=None, max_length=5000)
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = Field(default=None, max_length=50)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    conversation_type: str
    conversation_id: str
    user_id: str
    content: str | None
    media_url: str | None
    media_type: str | None
    is_pinned: bool
    pinned_by: str | None
    pinned_at: datetime | None   # ← was non-nullable, model says nullable
    created_at: datetime


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    total: int
    offset: int
    limit: int


class PinActionResponse(BaseModel):
    message_id: str
    is_pinned: bool
    message: str


class MessageDeleteResponse(BaseModel):
    message_id: str
    message: str