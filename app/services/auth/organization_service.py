"""
app/services/organization_service.py

Organization-scoped helpers:
  - Business name + phone availability checks (with Redis slots)
  - Slug generation (slugify + uniqueness loop)
  - System role lookup + assignment
  - Default permission seeding for new users

Services never commit — the router owns the transaction boundary.
"""

from __future__ import annotations

import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Permission, Role, User, UserPermission


# ── Slug ───────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """
    "Starlit Fitness & Wellness!" → "starlit-fitness-wellness"
    """
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


async def unique_slug(session: AsyncSession, business_name: str) -> str:
    """
    Generate a slug guaranteed unique in the DB.
    Tries clean slug first; appends a 4-hex suffix on collision.
    """
    base = slugify(business_name)
    slug = base
    for _ in range(10):
        hit = await session.execute(
            select(Organization.id).where(Organization.slug == slug)
        )
        if not hit.scalar_one_or_none():
            return slug
        slug = f"{base}-{secrets.token_hex(2)}"
    return f"{base}-{secrets.token_hex(4)}"


# ── Availability checks ────────────────────────────────────────────────────────

async def check_business_name_available(
    session: AsyncSession, business_name: str
) -> bool:
    """
    True if business_name is not yet taken (case-insensitive).

    Redis slot:
        # key = f"org:biz_name:{business_name.lower().strip()}"
        # cached = await redis.get(key)
        # if cached is not None:
        #     return cached == "1"
        # ... DB query below ...
        # await redis.setex(key, 30, "0" if taken else "1")
    """
    hit = await session.execute(
        select(Organization.id).where(
            func.lower(Organization.business_name) == business_name.lower().strip()
        )
    )
    return hit.scalar_one_or_none() is None


async def check_phone_available(session: AsyncSession, phone_number: str) -> bool:
    """
    True if phone_number is not yet registered to any organization.

    Redis slot: same pattern as check_business_name_available.
    """
    hit = await session.execute(
        select(Organization.id).where(
            Organization.phone_number == phone_number.strip()
        )
    )
    return hit.scalar_one_or_none() is None


# ── Role helpers ───────────────────────────────────────────────────────────────

async def get_system_role(session: AsyncSession, name: str) -> Role | None:
    result = await session.execute(
        select(Role).where(Role.name == name, Role.is_system == True)  # noqa: E712
    )
    return result.scalar_one_or_none()


def assign_role(user: User, role: Role) -> None:
    """Set the user's role FK. Does NOT grant any permissions."""
    user.role_id = role.id


# ── Permission seeding ─────────────────────────────────────────────────────────

# TODO: move this mapping into a DB seed/migration so permission changes
# don't require a redeploy. This dict is intentionally one-time seeding only
# — it has no effect on existing users.
DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "Super Admin": [
        "post.create", "post.delete", "post.moderate",
        "event.create", "event.cancel",
        "user.invite", "user.suspend", "user.elevate",
        "user.delete", "permission.grant",
        "branch.manage", "department.manage",
        "wellbeing.view_team", "wellbeing.export",
    ],
    "Employee": [
        "post.create",
    ],
}


async def seed_default_permissions(
    session: AsyncSession, user: User, role_name: str
) -> None:
    """
    Grant the starter permission set for role_name.
    granted_by=None marks them as system-seeded.
    One-time call at signup — editing this dict has no effect on existing users.
    """
    names = DEFAULT_PERMISSIONS.get(role_name)
    if not names:
        return
    result = await session.execute(
        select(Permission).where(Permission.name.in_(names))
    )
    for perm in result.scalars().all():
        session.add(UserPermission(
            user_id=user.id,
            permission_id=perm.id,
            organization_id=user.organization_id,
            granted_by=None,
        ))

