from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Permission, Role, RolePermission

BRANCH_ADMIN_EXCLUSIONS = {
    "member.role.assign", "member.permission.grant", "branch.update",
    "member.permission.revoke", "member.role.revoke",
}


async def ensure_organization_roles(session: AsyncSession, organization_id: str) -> dict[str, Role]:
    """Materialize editable organization copies of every global role template."""
    templates = (await session.execute(
        select(Role).where(Role.is_system.is_(True)).order_by(Role.name)
    )).scalars().all()
    existing = (await session.execute(
        select(Role).where(Role.organization_id == organization_id, Role.template_role_id.is_not(None))
    )).scalars().all()
    by_template = {role.template_role_id: role for role in existing}
    template_permissions = (await session.execute(
        select(RolePermission).where(RolePermission.role_id.in_([role.id for role in templates]))
    )).scalars().all() if templates else []
    permissions_by_template: dict[str, list[RolePermission]] = {}
    for grant in template_permissions:
        permissions_by_template.setdefault(grant.role_id, []).append(grant)

    for template in templates:
        if template.id in by_template:
            continue
        role = Role(
            name=template.name, description=template.description,
            organization_id=organization_id, branch_id=None, is_system=False,
            is_unique=template.is_unique, unique_scope=template.unique_scope,
            template_role_id=template.id,
        )
        session.add(role)
        await session.flush()
        for grant in permissions_by_template.get(template.id, []):
            session.add(RolePermission(
                role_id=role.id, permission_id=grant.permission_id, scope=grant.scope,
            ))
        by_template[template.id] = role
    return {template.name: by_template[template.id] for template in templates}


async def ensure_branch_roles(session: AsyncSession, organization_id: str, branch_id: str) -> list[Role]:
    """Create branch-local copies of every organization role without mutating org defaults."""
    organization_roles = (await session.execute(select(Role).where(
        Role.organization_id == organization_id, Role.branch_id.is_(None),
        Role.template_role_id.is_not(None),
    ).order_by(Role.name))).scalars().all()
    existing = (await session.execute(select(Role).where(
        Role.organization_id == organization_id, Role.branch_id == branch_id,
    ))).scalars().all()
    by_source = {role.template_role_id: role for role in existing}
    for source in organization_roles:
        if source.id in by_source:
            continue
        role = Role(
            name=source.name, description=source.description, organization_id=organization_id,
            branch_id=branch_id, is_system=False, is_unique=source.is_unique,
            unique_scope=source.unique_scope, template_role_id=source.id,
        )
        session.add(role)
        await session.flush()
        grants = (await session.execute(
            select(RolePermission, Permission).join(Permission).where(
                RolePermission.role_id == source.id, Permission.scope != "org",
            )
        )).all()
        if source.name == "member":
            grants = []
        elif source.name == "admin":
            grants = [(grant, permission) for grant, permission in grants if permission.name not in BRANCH_ADMIN_EXCLUSIONS]
        elif source.name == "branch_manager":
            grants = [(grant, permission) for grant, permission in grants if permission.scope == "both"]
        for grant, _permission in grants:
            session.add(RolePermission(role_id=role.id, permission_id=grant.permission_id, scope="branch"))
        by_source[source.id] = role
    return [by_source[source.id] for source in organization_roles]
