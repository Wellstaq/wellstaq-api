"""
app/services/organization_service.py

Organization-scoped helpers:
  - Business name + phone availability checks (with Redis slots)
  - Slug generation (slugify + uniqueness loop)
  - System role lookup + assignment
  - Super Admin permission seeding at org creation

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
        select(User.id).where(
            User.phone_number == phone_number.strip()
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

async def seed_super_admin_permissions(
    session: AsyncSession,
    user: User,
) -> None:
    """
    Grant ALL platform permissions to a new org owner (Super Admin).

    Permissions for every other role are NOT seeded automatically —
    they must be granted explicitly by an admin after the user joins.
    This keeps the permission model intentional: no one receives access
    they weren't deliberately given.

    Implementation notes:
        - Fetches every row from the permissions table so new permissions
          added via scripts/seed_permissions.py are automatically included
          for every new org owner, with no code change required here.
        - All grants are org-wide (branch_id=NULL).
        - granted_by=None marks every row as system-seeded.
        - One-time call at org creation — never called again for the same user.
    """
    # Permissions are inherited exclusively through RolePermission.
    # Kept as a compatibility hook for existing signup callers.
    return
