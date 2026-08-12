from __future__ import annotations

from pydantic import BaseModel


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