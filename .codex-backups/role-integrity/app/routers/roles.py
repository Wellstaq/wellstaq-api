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
from app.services.organization_roles import ensure_branch_roles, ensure_organization_roles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["roles"])

PROTECTED_EDIT_TARGETS: dict[str, set[str]] = {
    "admin": {"super_admin", "admin", "branch_manager"},
    "branch_manager": {"super_admin", "branch_manager"},
    "team_leader": {"super_admin", "admin", "branch_manager", "team_leader"},
    "member": {"super_admin", "admin", "branch_manager", "team_leader", "member"},
    "employee": {"super_admin", "admin", "branch_manager", "team_leader", "member"},
}


# ── Schemas ────────────────────────────────────────────────────────────────────

class RoleItem(BaseModel):
    id: str
    name: str
    description: str | None
    branch_id: str | None = None
    is_system: bool = True
    is_default: bool = False
    user_count: int = 0
    permissions: list[str] = Field(default_factory=list)
    permission_scopes: dict[str, str] = Field(default_factory=dict)

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
    branch_id: str | None = None


class OrganizationRolesResponse(BaseModel):
    items: list[RoleItem]
    total: int


class RolePermissionsUpdateRequest(BaseModel):
    permission_names: list[str]


class MemberRoleUpdateRequest(BaseModel):
    role_id: str | None


class RoleCopyRequest(BaseModel):
    name: str


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


async def _permission_scope_map(db: AsyncSession, role_ids: list[str]) -> dict[str, dict[str, str]]:
    if not role_ids:
        return {}
    rows = (await db.execute(
        select(RolePermission.role_id, Permission.name, RolePermission.scope)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(RolePermission.role_id.in_(role_ids))
    )).all()
    result: dict[str, dict[str, str]] = {}
    for role_id, permission_name, scope in rows:
        result.setdefault(role_id, {})[permission_name] = scope
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
        await ensure_organization_roles(db, org_id)
        await db.flush()
        if branch_id:
            branch = (await db.execute(select(Branch).where(
                Branch.id == branch_id, Branch.organization_id == org_id,
            ))).scalar_one_or_none()
            if branch is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found.")
            await ensure_branch_roles(db, org_id, branch_id)
            await db.commit()
        filters = [Role.organization_id == org_id, Role.branch_id == branch_id if branch_id else Role.branch_id.is_(None)]
        if branch_id:
            filters.append(Role.name != "super_admin")
            default_org_role_ids = select(Role.id).where(
                Role.organization_id == org_id,
                Role.branch_id.is_(None),
                Role.template_role_id.is_not(None),
            )
            filters.append(or_(Role.template_role_id.is_(None), Role.template_role_id.in_(default_org_role_ids)))
        else:
            # Overview owns only the Super Admin default and custom roles
            # explicitly created at organization scope. Other default roles
            # are branch roles and must never leak into Overview.
            filters.append(or_(
                Role.template_role_id.is_(None),
                Role.name == "super_admin",
            ))
        rows = (await db.execute(
            select(Role, func.count(func.distinct(User.id)).label("user_count"))
            .outerjoin(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
            .outerjoin(User, or_(User.role_id == Role.id, User.id == UserRoleAssignment.user_id))
            .where(*filters)
            .group_by(Role.id)
            .order_by(Role.is_system.desc(), Role.name)
        )).all()
        permissions = await _permission_map(db, [role.id for role, _ in rows])
        permission_scopes = await _permission_scope_map(db, [role.id for role, _ in rows])
        items = [RoleItem(
            id=role.id, name=role.name, description=role.description,
            branch_id=role.branch_id, is_system=False, is_default=role.template_role_id is not None, user_count=user_count,
            permissions=permissions.get(role.id, []),
            permission_scopes=permission_scopes.get(role.id, {}),
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
        if body.branch_id:
            branch = (await db.execute(select(Branch).where(
                Branch.id == body.branch_id, Branch.organization_id == org_id,
            ))).scalar_one_or_none()
            if branch is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found.")
        name = body.name.strip()
        exists = (await db.execute(select(Role.id).where(
            Role.organization_id == org_id, Role.branch_id == body.branch_id,
            func.lower(Role.name) == name.lower(),
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
    current_user: CurrentUser = Depends(require_permission("member.role.revoke")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles/{role_id}"):
        role = (await db.execute(select(Role).where(Role.id == role_id, Role.organization_id == org_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found.")
        if role.is_system or role.template_role_id is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Default roles cannot be deleted, but their organization permissions can be edited.")
        await require_scoped_permission(
            db=db, current_user=current_user, permission="member.role.revoke", target_branch_id=None,
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
    db: AsyncSession, current_user: CurrentUser, permission_names: list[str], branch_id: str | None,
) -> list[Permission]:
    permissions = (await db.execute(select(Permission).where(Permission.name.in_(set(permission_names))))).scalars().all()
    if len(permissions) != len(set(permission_names)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more permissions are unknown.")
    for permission in permissions:
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
        await require_scoped_permission(db=db, current_user=current_user, permission="member.role.assign", target_branch_id=None)
        source_names = (await db.execute(
            select(Permission.name).join(RolePermission).where(RolePermission.role_id == source.id)
        )).scalars().all()
        permissions = await _require_permission_ceiling(db, current_user, list(source_names), None)
        role = Role(name=body.name.strip(), description=source.description, organization_id=org_id,
                    branch_id=None, is_system=False)
        db.add(role)
        await db.flush()
        db.add_all([RolePermission(role_id=role.id, permission_id=p.id, scope="org" if p.scope == "org" else "branch") for p in permissions])
        await db.commit()
        return RoleItem(id=role.id, name=role.name, description=role.description, branch_id=role.branch_id,
                        is_system=False, permissions=sorted(source_names))


@router.put("/organizations/{org_id}/roles/{role_id}/permissions", response_model=RoleItem)
async def update_role_permissions(
    org_id: str, role_id: str, body: RolePermissionsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> RoleItem:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/roles/{role_id}/permissions"):
        role = (await db.execute(select(Role).where(Role.id == role_id, Role.organization_id == org_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found.")
        if role.is_system:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform role templates are read-only.")
        existing = (await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))).scalars().all()
        existing_names = set((await db.execute(
            select(Permission.name).join(RolePermission).where(RolePermission.role_id == role.id)
        )).scalars().all())
        requested_names = set(body.permission_names)
        if role.name == "member" and requested_names:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The Member role has no permissions by default. Grant a direct permission or assign another role instead.")
        if role.name == "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The Super Admin role permissions cannot be edited.")
        if role.name in PROTECTED_EDIT_TARGETS.get(current_user.role or "member", set()):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"The {current_user.role or 'member'} role cannot edit permissions for {role.name}.",
            )
        if current_user.role == "branch_manager" and role.name == "admin" and role.branch_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "A Branch Manager can edit Admin permissions only within an assigned branch.")
        current = (await db.execute(select(User).where(User.id == current_user.user_id))).scalar_one()
        current_role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        if current_role is not None and (
            current_role.id == role.id
            or (role.branch_id is not None and role.template_role_id == current_role.id)
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot edit permissions for a role assigned to your own account.")
        target_scope = role.branch_id
        if requested_names - existing_names:
            await require_scoped_permission(db=db, current_user=current_user, permission="member.permission.grant", target_branch_id=target_scope)
        if existing_names - requested_names:
            await require_scoped_permission(db=db, current_user=current_user, permission="member.permission.revoke", target_branch_id=target_scope)
        permissions = await _require_permission_ceiling(db, current_user, body.permission_names, target_scope)
        if role.branch_id and any(permission.scope == "org" for permission in permissions):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organization-only permissions cannot be added to a branch role.")
        if role.branch_id is None and any(permission.scope == "branch" for permission in permissions):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Branch-only permissions cannot be added to an organization role.")
        previous_scope = {
            permission_id: scope for permission_id, scope in (await db.execute(
                select(RolePermission.permission_id, RolePermission.scope).where(RolePermission.role_id == role.id)
            )).all()
        }
        for item in existing:
            await db.delete(item)
        await db.flush()
        db.add_all([RolePermission(
            role_id=role.id, permission_id=p.id,
            scope="branch" if role.branch_id else "org",
        ) for p in permissions])
        await db.commit()
        return RoleItem(id=role.id, name=role.name, description=role.description, branch_id=role.branch_id,
                        is_system=False, is_default=role.template_role_id is not None,
                        permissions=sorted(p.name for p in permissions),
                        permission_scopes={p.name: "branch" if role.branch_id else "org" for p in permissions})


@router.put("/organizations/{org_id}/members/{user_id}/system-role", status_code=status.HTTP_204_NO_CONTENT)
async def change_member_system_role(
    org_id: str, user_id: str, body: MemberRoleUpdateRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_permission("member.role.assign")),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/system-role"):
        target = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
        role = (await db.execute(select(Role).where(
            Role.id == body.role_id, Role.organization_id == org_id, Role.template_role_id.is_not(None),
        ))).scalar_one_or_none()
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
        if body.role_id:
            role = (await db.execute(select(Role).where(
                Role.id == body.role_id, Role.organization_id == org_id,
                Role.is_system.is_(False), Role.template_role_id.is_(None),
            ))).scalar_one_or_none()
            if role is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found.")
            await require_scoped_permission(db=db, current_user=current_user, permission="member.role.assign", target_branch_id=role.branch_id or target.branch_id)
            target.role_id = role.id
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A user must always have exactly one role.")
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/organizations/{org_id}/members/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_member_role(
    org_id: str, user_id: str, branch_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("member.role.revoke")),
):
    """Revoke the current scoped role and immediately fall back to Member."""
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/members/{user_id}/role"):
        target = (await db.execute(select(User).where(
            User.id == user_id, User.organization_id == org_id,
        ))).scalar_one_or_none()
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        if target.id == current_user.user_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot revoke your own role.")
        current_role = (await db.execute(select(Role).where(Role.id == target.role_id))).scalar_one_or_none()
        if current_role is not None and current_role.name == "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The Super Admin role cannot be revoked.")
        if branch_id and target.branch_id != branch_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The user does not belong to the selected branch.")
        if current_role is not None and current_role.branch_id != branch_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Revoke this role from the scope where it was assigned.")
        await require_scoped_permission(
            db=db, current_user=current_user, permission="member.role.revoke", target_branch_id=branch_id,
        )
        if branch_id:
            await ensure_branch_roles(db, org_id, branch_id)
        else:
            await ensure_organization_roles(db, org_id)
        await db.flush()
        member_role = (await db.execute(select(Role).where(
            Role.organization_id == org_id,
            Role.branch_id == branch_id if branch_id else Role.branch_id.is_(None),
            Role.name == "member",
            Role.template_role_id.is_not(None),
        ))).scalar_one_or_none()
        if member_role is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Member fallback role is unavailable.")
        target.role_id = member_role.id
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
    branch_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PermissionsResponse:
    async with request_guard(db, "/permissions"):
        scope_filter = Permission.scope != "org" if branch_id else Permission.scope != "branch"
        result = await db.execute(select(Permission).where(scope_filter).order_by(Permission.name))
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
