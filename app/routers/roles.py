"""
app/routers/roles.py

Role and permission reference data.

These are read-only lookup endpoints for populating frontend dropdowns
(e.g. invite form role selector, permission assignment UI).

Routes:
  GET  /roles/system       — list all system roles
  GET  /permissions        — list all permissions
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import Role
from app.models.permission import Permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["roles"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class RoleItem(BaseModel):
    id: str
    name: str
    description: str | None

    model_config = {"from_attributes": True}


class PermissionItem(BaseModel):
    id: str
    name: str
    description: str | None

    model_config = {"from_attributes": True}


class SystemRolesResponse(BaseModel):
    items: list[RoleItem]
    total: int


class PermissionsResponse(BaseModel):
    items: list[PermissionItem]
    total: int


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get(
    "/roles/system",
    response_model=SystemRolesResponse,
    status_code=status.HTTP_200_OK,
    summary="List system roles",
    description="Returns all system-level roles. Useful for populating invite and role-assignment dropdowns.",
)
async def list_system_roles(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SystemRolesResponse:
    async with request_guard(db, "/roles/system"):
        result = await db.execute(
            select(Role)
            .where(Role.is_system == True)
            .order_by(Role.name)
        )
        roles = result.scalars().all()

        items = [
            RoleItem(
                id=str(r.id),
                name=r.name,
                description=getattr(r, "description", None),
            )
            for r in roles
        ]
        return SystemRolesResponse(items=items, total=len(items))


@router.get(
    "/permissions",
    response_model=PermissionsResponse,
    status_code=status.HTTP_200_OK,
    summary="List all permissions",
    description="Returns all available permissions. Useful for building role-assignment and invite UIs.",
)
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PermissionsResponse:
    async with request_guard(db, "/permissions"):
        result = await db.execute(
            select(Permission).order_by(Permission.name)
        )
        permissions = result.scalars().all()

        items = [
            PermissionItem(
                id=str(p.id),
                name=p.name,
                description=getattr(p, "description", None),
            )
            for p in permissions
        ]
        return PermissionsResponse(items=items, total=len(items))