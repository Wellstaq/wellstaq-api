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

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_permission, require_same_organization, require_scoped_permission
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import Branch, Role, User
from app.models.permission import Permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["roles"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class RoleItem(BaseModel):
    id: str
    name: str
    description: str | None
    branch_id: str | None = None
    is_system: bool = True
    user_count: int = 0

    model_config = {"from_attributes": True}


class PermissionItem(BaseModel):
    id: str
    name: str
    description: str | None
    category: str | None = None
    scope: str

    model_config = {"from_attributes": True}


class SystemRolesResponse(BaseModel):
    items: list[RoleItem]
    total: int
    model_config = {"from_attributes": True}



class PermissionsResponse(BaseModel):
    items: list[PermissionItem]
    total: int


class RoleCreateRequest(BaseModel):
    name: str
    description: str | None = None
    branch_id: str


class OrganizationRolesResponse(BaseModel):
    items: list[RoleItem]
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
    assignable_only: bool = Query(default=False),
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
        if assignable_only:
            roles = [role for role in roles if role.name != "super_admin"]
        items = [
            RoleItem(
                id=str(r.id),
                name=r.name,
                description=getattr(r, "description", None),
                branch_id=None,
                is_system=True,
                user_count=0,
            )
            for r in roles
        ]
        return SystemRolesResponse(items=items, total=len(items))


@router.get("/organizations/{org_id}/roles", response_model=OrganizationRolesResponse)
async def list_organization_roles(
    org_id: str,
    branch_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> OrganizationRolesResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles"):
        filters = [or_(Role.is_system.is_(True), Role.organization_id == org_id)]
        if branch_id is not None:
            filters.append(or_(Role.is_system.is_(True), Role.branch_id == branch_id))
        rows = (await db.execute(
            select(Role, func.count(User.id).label("user_count"))
            .outerjoin(User, User.role_id == Role.id)
            .where(*filters)
            .group_by(Role.id)
            .order_by(Role.is_system.desc(), Role.name)
        )).all()
        items = [RoleItem(
            id=role.id, name=role.name, description=role.description,
            branch_id=role.branch_id, is_system=role.is_system, user_count=user_count,
        ) for role, user_count in rows]
        return OrganizationRolesResponse(items=items, total=len(items))


@router.post("/organizations/{org_id}/roles", response_model=RoleItem, status_code=status.HTTP_201_CREATED)
async def create_organization_role(
    org_id: str,
    body: RoleCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.role.assign")),
) -> RoleItem:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles"):
        await require_scoped_permission(
            db=db, current_user=current_user, permission="member.role.assign", target_branch_id=body.branch_id,
        )
        branch = (await db.execute(select(Branch).where(
            Branch.id == body.branch_id, Branch.organization_id == org_id,
        ))).scalar_one_or_none()
        if branch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found.")
        name = body.name.strip()
        exists = (await db.execute(select(Role.id).where(
            Role.organization_id == org_id, Role.branch_id == body.branch_id, func.lower(Role.name) == name.lower(),
        ))).scalar_one_or_none()
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "A role with this name already exists in the branch.")
        role = Role(
            name=name, description=body.description.strip() if body.description else None,
            organization_id=org_id, branch_id=body.branch_id, is_system=False,
        )
        db.add(role)
        await db.commit()
        await db.refresh(role)
        return RoleItem(id=role.id, name=role.name, description=role.description, branch_id=role.branch_id, is_system=False, user_count=0)


@router.delete("/organizations/{org_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization_role(
    org_id: str,
    role_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.role.assign")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles/{role_id}"):
        role = (await db.execute(select(Role).where(Role.id == role_id, Role.organization_id == org_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found.")
        if role.is_system:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "System roles cannot be deleted.")
        await require_scoped_permission(
            db=db, current_user=current_user, permission="member.role.assign", target_branch_id=role.branch_id,
        )
        assigned = (await db.execute(select(func.count()).select_from(User).where(User.role_id == role.id))).scalar_one()
        if assigned:
            raise HTTPException(status.HTTP_409_CONFLICT, "Move users out of this role before deleting it.")
        await db.delete(role)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


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
                category=p.category,
                scope=p.scope,
            )
            for p in permissions
        ]
        return PermissionsResponse(items=items, total=len(items))
