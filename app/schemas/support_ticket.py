from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TicketCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    subject: str = Field(min_length=2, max_length=100)
    message: str = Field(min_length=10, max_length=5000)


class TicketCreateResponse(BaseModel):
    accepted: bool
    ticket_id: str
    status: str


class TicketStatusRequest(BaseModel):
    status: str = Field(pattern="^(open|in_progress|resolved|closed)$")


class TicketAdminItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    organization_id: str
    name: str
    email: str
    subject: str
    message: str
    status: str
    created_at: str


class TicketAdminListResponse(BaseModel):
    items: list[TicketAdminItem]
    total: int


class TicketStatusResponse(BaseModel):
    id: str
    status: str