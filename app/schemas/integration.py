from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConnectIntegrationResponse(BaseModel):
    authorization_url: str


class MyIntegrationItem(BaseModel):
    provider: str
    status: str
    enabled: bool
    last_synced_at: datetime | None


class MyIntegrationListResponse(BaseModel):
    items: list[MyIntegrationItem]


class SyncResultResponse(BaseModel):
    synced_entries: int
    challenge_ids: list[str]


class IntegrationItem(BaseModel):
    id: str
    name: str
    description: str
    status: str
    enabled: bool


class IntegrationListResponse(BaseModel):
    items: list[IntegrationItem]


class ToggleIntegrationRequest(BaseModel):
    enabled: bool


class ToggleIntegrationResponse(BaseModel):
    enabled: bool