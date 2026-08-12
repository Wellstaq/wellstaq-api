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
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_permission, require_same_organization, require_scoped_permission
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import Branch, Role, RolePermission, User, UserRoleAssignment
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
    permissions: list[str] = Field(default_factory=list)

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


class RolePermissionsUpdateRequest(BaseModel):
    permission_names: list[str]


class MemberRoleUpdateRequest(BaseModel):
    role_id: str | None


class RoleCopyRequest(BaseModel):
    name: str
    branch_id: str


async def _permission_map(db: AsyncSession, role_ids: list[str]) -> dict[str, list[str]]:
    if not role_ids:
        return {}
    rows = (await db.execute(
        select(RolePermission.role_id, Permission.name)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(RolePermission.role_id.in_(role_ids))
        .order_by(Permission.name)
    )).all()
    result: dict[str, list[str]] = {}
    for role_id, permission_name in rows:
        result.setdefault(role_id, []).append(permission_name)
    return result


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
        permissions = await _permission_map(db, [role.id for role in roles])
        items = [
            RoleItem(
                id=str(r.id),
                name=r.name,
                description=getattr(r, "description", None),
                branch_id=None,
                is_system=True,
                user_count=0,
                permissions=permissions.get(r.id, []),
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
            select(Role, func.count(func.distinct(User.id)).label("user_count"))
            .outerjoin(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
            .outerjoin(User, or_(User.role_id == Role.id, User.id == UserRoleAssignment.user_id))
            .where(*filters)
            .group_by(Role.id)
            .order_by(Role.is_system.desc(), Role.name)
        )).all()
        permissions = await _permission_map(db, [role.id for role, _ in rows])
        items = [RoleItem(
            id=role.id, name=role.name, description=role.description,
            branch_id=role.branch_id, is_system=role.is_system, user_count=user_count,
            permissions=permissions.get(role.id, []),
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
        assigned = (await db.execute(select(func.count()).select_from(UserRoleAssignment).where(
            UserRoleAssignment.role_id == role.id,
        ))).scalar_one()
        if assigned:
            raise HTTPException(status.HTTP_409_CONFLICT, "Move users out of this role before deleting it.")
        await db.delete(role)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _require_permission_ceiling(
    db: AsyncSession, current_user: CurrentUser, permission_names: list[str], branch_id: str,
) -> list[Permission]:
    permissions = (await db.execute(select(Permission).where(Permission.name.in_(set(permission_names))))).scalars().all()
    if len(permissions) != len(set(permission_names)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more permissions are unknown.")
    for permission in permissions:
        if permission.scope == "org":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{permission.name} is organization-only and cannot be added to a branch custom role.")
        await require_scoped_permission(
            db=db, current_user=current_user, permission=permission.name, target_branch_id=branch_id,
        )
    return permissions


@router.post("/organizations/{org_id}/roles/{role_id}/copy", response_model=RoleItem, status_code=status.HTTP_201_CREATED)
async def copy_role(
    org_id: str, role_id: str, body: RoleCopyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.role.assign")),
) -> RoleItem:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles/{role_id}/copy"):
        source = (await db.execute(select(Role).where(
            Role.id == role_id, or_(Role.is_system.is_(True), Role.organization_id == org_id),
        ))).scalar_one_or_none()
        if source is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")
        await require_scoped_permission(db=db, current_user=current_user, permission="member.role.assign", target_branch_id=body.branch_id)
        source_names = (await db.execute(
            select(Permission.name).join(RolePermission).where(RolePermission.role_id == source.id, Permission.scope != "org")
        )).scalars().all()
        permissions = await _require_permission_ceiling(db, current_user, list(source_names), body.branch_id)
        role = Role(name=body.name.strip(), description=source.description, organization_id=org_id,
                    branch_id=body.branch_id, is_system=False)
        db.add(role)
        await db.flush()
        db.add_all([RolePermission(role_id=role.id, permission_id=p.id, scope="branch") for p in permissions])
        await db.commit()
        return RoleItem(id=role.id, name=role.name, description=role.description, branch_id=role.branch_id,
                        is_system=False, permissions=sorted(source_names))


@router.put("/organizations/{org_id}/roles/{role_id}/permissions", response_model=RoleItem)
async def update_role_permissions(
    org_id: str, role_id: str, body: RolePermissionsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.permission.grant")),
) -> RoleItem:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles/{role_id}/permissions"):
        role = (await db.execute(select(Role).where(Role.id == role_id, Role.organization_id == org_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found.")
        if role.is_system:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "System role permissions are read-only. Copy the role to customize it.")
        await require_scoped_permission(db=db, current_user=current_user, permission="member.permission.grant", target_branch_id=role.branch_id)
        permissions = await _require_permission_ceiling(db, current_user, body.permission_names, role.branch_id)
        existing = (await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))).scalars().all()
        for item in existing:
            await db.delete(item)
        db.add_all([RolePermission(role_id=role.id, permission_id=p.id, scope="branch") for p in permissions])
        await db.commit()
        return RoleItem(id=role.id, name=role.name, description=role.description, branch_id=role.branch_id,
                        is_system=False, permissions=sorted(p.name for p in permissions))


@router.put("/organizations/{org_id}/members/{user_id}/system-role", status_code=status.HTTP_204_NO_CONTENT)
async def change_member_system_role(
    org_id: str, user_id: str, body: MemberRoleUpdateRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_permission("member.role.assign")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/system-role"):
        target = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
        role = (await db.execute(select(Role).where(Role.id == body.role_id, Role.is_system.is_(True)))).scalar_one_or_none()
        if target is None or role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User or system role not found.")
        if role.name == "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin is assigned by the system and cannot be granted manually.")
        await require_scoped_permission(db=db, current_user=current_user, permission="member.role.assign", target_branch_id=target.branch_id)
        target.role_id = role.id
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/organizations/{org_id}/members/{user_id}/custom-role", status_code=status.HTTP_204_NO_CONTENT)
async def change_member_custom_role(
    org_id: str, user_id: str, body: MemberRoleUpdateRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_permission("member.role.assign")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/custom-role"):
        target = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        existing = (await db.execute(select(UserRoleAssignment).join(Role).where(
            UserRoleAssignment.user_id == user_id, Role.is_system.is_(False),
        ))).scalars().all()
        for assignment in existing:
            await db.delete(assignment)
        if body.role_id:
            role = (await db.execute(select(Role).where(
                Role.id == body.role_id, Role.organization_id == org_id, Role.is_system.is_(False),
            ))).scalar_one_or_none()
            if role is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found.")
            await require_scoped_permission(db=db, current_user=current_user, permission="member.role.assign", target_branch_id=role.branch_id)
            db.add(UserRoleAssignment(user_id=user_id, role_id=role.id, branch_id=role.branch_id))
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
