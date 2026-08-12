"""
app/schemas/notification.py
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str | None
    actor_id: str | None
    notification_type: str
    reference_id: str | None
    reference_type: str | None
    body: str
    image_url: str | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int
    offset: int
    limit: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class MarkReadResponse(BaseModel):
    notification_id: str
    is_read: bool
    message: str


class MarkAllReadResponse(BaseModel):
    marked_count: int
    message: str