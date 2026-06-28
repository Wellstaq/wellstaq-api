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

# Permissions granted to non-owner roles at invite acceptance.
# Super Admin is intentionally absent — they receive ALL platform
# permissions via seed_default_permissions(), not a fixed subset.
#
# Keep this list in sync with the permission names in
# scripts/seed_permissions.py. Adding a new permission to the seed
# script does NOT automatically grant it to existing employees —
# this mapping only applies at the moment a new user is created.
DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "Employee": [
        "post.delete",       # can delete their own posts
        "post.moderate",     # can flag/report content
    ],
    "Branch Manager": [
        "member.invite",
        "member.suspend",
        "member.reinstate",
        "member.department.assign",
        "department.create",
        "department.update",
        "club.create",
        "club.update",
        "club.member.remove",
        "event.create",
        "event.update",
        "event.delete",
        "challenge.create",
        "challenge.update",
        "post.delete",
        "post.moderate",
        "story.delete",
        "comment.delete",
        "wellbeing.view_team",
        "wellbeing.export",
    ],
    "Team Leader": [
        "member.suspend",
        "member.reinstate",
        "member.department.assign",
        "post.delete",
        "post.moderate",
        "comment.delete",
        "wellbeing.view_team",
    ],
}


async def seed_default_permissions(
    session: AsyncSession,
    user: User,
    role_name: str,
) -> None:
    """
    Grant the starter permission set for role_name at signup/invite acceptance.

    Super Admin (org owner):
        Receives ALL platform permissions — fetched live from the permissions
        table so new permissions added via the seed script are automatically
        included for every new org owner, with no code change required here.
        All grants are org-wide (branch_id=NULL).

    Other roles (Branch Manager, Team Leader, Employee):
        Receive the fixed subset defined in DEFAULT_PERMISSIONS above.
        These are org-wide grants at creation time; branch-scoped grants
        are added separately by an admin post-join.

    granted_by=None marks every row as system-seeded (not manually granted).
    One-time call at user creation — this has no effect on existing users.
    """
    if role_name == "Super Admin":
        # Fetch every permission that exists in the platform.
        # This means the org owner always has full authority over their org
        # regardless of which permissions were added to the seed catalogue
        # after their account was created — new permissions are granted to
        # NEW org owners only. Existing owners are unaffected (the service
        # is called once, at signup, never again).
        result = await session.execute(select(Permission))
        permissions = result.scalars().all()
    else:
        names = DEFAULT_PERMISSIONS.get(role_name)
        if not names:
            return
        result = await session.execute(
            select(Permission).where(Permission.name.in_(names))
        )
        permissions = result.scalars().all()

    for perm in permissions:
        session.add(UserPermission(
            user_id=user.id,
            permission_id=perm.id,
            organization_id=user.organization_id,
            branch_id=None,   # org-wide — owner has authority across the whole org
            granted_by=None,  # system-seeded, not manually granted
        ))