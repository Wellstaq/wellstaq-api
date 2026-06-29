"""
scripts/seed_permissions.py

Platform permission + system role seeder.

Run once during initial setup, and again whenever new permissions or roles are added.
Existing rows are never touched — only missing ones are inserted.

Usage:
    python -m scripts.seed_permissions

Run order (first-time setup):
    1. python -m scripts.seed_permissions   ← roles + permissions
    2. Start the app / register org owner
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.permission import Permission, Role

logger = logging.getLogger(__name__)


# ── System role catalogue ──────────────────────────────────────────────────────
#
# System roles are platform-owned:
#   is_system=True, organization_id=NULL, branch_id=NULL
#
# These are the only roles that exist before any org is created.
# Custom org/branch roles are created at runtime by org admins.
#
# RoleName constants (from app.core.constants) must match these names exactly.

SYSTEM_ROLES: list[dict] = [
    {
        "name": "super_admin",
        "description": (
            "Organization owner. Receives all platform permissions at registration. "
            "Has full authority across the entire org."
        ),
    },
    {
        "name": "admin",
        "description": (
            "Organization administrator. Permissions granted explicitly by the super admin."
        ),
    },
    {
        "name": "branch_manager",
        "description": (
            "Manages a specific branch. Permissions scoped to that branch, "
            "granted explicitly by an admin."
        ),
    },

     {
        "name": "team_leader",        
        "description": (
            "Leads a team within a branch or org. Permissions granted explicitly "
            "by a branch manager or admin."
        ),
    },
    {
        "name": "member",
        "description": (
            "Standard employee. No permissions by default — "
            "granted individually by an admin."
        ),
    },
]


# ── Permission catalogue ───────────────────────────────────────────────────────

PERMISSIONS: list[dict] = [

    # ── Member management ──────────────────────────────────────────────────────
    {
        "name": "member.invite",
        "description": "Invite new users to the organization or a branch",
        "category": "member",
        "scope": "both",
    },
    {
        "name": "member.remove",
        "description": "Remove a user from the organization or a branch",
        "category": "member",
        "scope": "both",
    },
    {
        "name": "member.suspend",
        "description": "Suspend a user (set status=suspended)",
        "category": "member",
        "scope": "both",
    },
    {
        "name": "member.reinstate",
        "description": "Lift a suspension and restore a user to active status",
        "category": "member",
        "scope": "both",
    },
    {
        "name": "member.role.assign",
        "description": "Assign or change a user's role",
        "category": "member",
        "scope": "org",
    },
    {
        "name": "member.role.revoke",
        "description": "Revoke a user's current role",
        "category": "member",
        "scope": "org",
    },
    {
        "name": "member.department.assign",
        "description": "Move a user into or out of a department",
        "category": "member",
        "scope": "both",
    },
    {
        "name": "member.branch.assign",
        "description": "Transfer a user to a different branch",
        "category": "member",
        "scope": "org",
    },
    {
        "name": "member.permission.grant",
        "description": "Grant a permission to another user (subject to grant ceiling)",
        "category": "member",
        "scope": "both",
    },
    {
        "name": "member.permission.revoke",
        "description": "Revoke a permission from another user",
        "category": "member",
        "scope": "both",
    },

    # ── Branch ─────────────────────────────────────────────────────────────────
    {
        "name": "branch.create",
        "description": "Create a new branch within the organization",
        "category": "branch",
        "scope": "org",
    },
    {
        "name": "branch.update",
        "description": "Edit branch details (name, location, settings)",
        "category": "branch",
        "scope": "both",
    },
    {
        "name": "branch.delete",
        "description": "Delete a branch and all its data",
        "category": "branch",
        "scope": "org",
    },
    {
        "name": "branch.manager.assign",
        "description": "Designate or replace the manager of a branch",
        "category": "branch",
        "scope": "org",
    },

    # ── Department ─────────────────────────────────────────────────────────────
    {
        "name": "department.create",
        "description": "Create a new department within the org or a branch",
        "category": "department",
        "scope": "both",
    },
    {
        "name": "department.update",
        "description": "Edit department details (name, description, rank)",
        "category": "department",
        "scope": "both",
    },
    {
        "name": "department.delete",
        "description": "Delete a department",
        "category": "department",
        "scope": "both",
    },

    # ── Club ───────────────────────────────────────────────────────────────────
    {
        "name": "club.create",
        "description": "Create a new club within the org or a branch",
        "category": "club",
        "scope": "both",
    },
    {
        "name": "club.update",
        "description": "Edit club details",
        "category": "club",
        "scope": "both",
    },
    {
        "name": "club.delete",
        "description": "Delete a club",
        "category": "club",
        "scope": "both",
    },
    {
        "name": "club.member.remove",
        "description": "Remove a user from a club",
        "category": "club",
        "scope": "both",
    },

    # ── Events ─────────────────────────────────────────────────────────────────
    {
        "name": "event.create",
        "description": "Create an org or branch event",
        "category": "event",
        "scope": "both",
    },
    {
        "name": "event.update",
        "description": "Edit an existing event",
        "category": "event",
        "scope": "both",
    },
    {
        "name": "event.delete",
        "description": "Delete an event",
        "category": "event",
        "scope": "both",
    },

    # ── Challenges ─────────────────────────────────────────────────────────────
    {
        "name": "challenge.create",
        "description": "Create a wellness or fitness challenge",
        "category": "challenge",
        "scope": "both",
    },
    {
        "name": "challenge.update",
        "description": "Edit an existing challenge",
        "category": "challenge",
        "scope": "both",
    },
    {
        "name": "challenge.delete",
        "description": "Delete a challenge",
        "category": "challenge",
        "scope": "both",
    },

    # ── Content moderation ─────────────────────────────────────────────────────
    {
        "name": "post.delete",
        "description": "Permanently delete any post within scope",
        "category": "content",
        "scope": "both",
    },
    {
        "name": "post.moderate",
        "description": "Pin, hide, or flag posts without deleting them",
        "category": "content",
        "scope": "both",
    },
    {
        "name": "story.delete",
        "description": "Delete any story within scope",
        "category": "content",
        "scope": "both",
    },
    {
        "name": "comment.delete",
        "description": "Delete any comment within scope",
        "category": "content",
        "scope": "both",
    },

    # ── Wellbeing & reporting ──────────────────────────────────────────────────
    {
        "name": "wellbeing.view_team",
        "description": "View aggregated wellbeing scores for a team or branch",
        "category": "wellbeing",
        "scope": "both",
    },
    {
        "name": "wellbeing.export",
        "description": "Export wellbeing reports to CSV or PDF",
        "category": "wellbeing",
        "scope": "both",
    },

    # ── Org settings (super_admin territory) ───────────────────────────────────
    {
        "name": "org.settings.update",
        "description": "Update organization-level settings and profile",
        "category": "org",
        "scope": "org",
    },
    {
        "name": "org.billing.manage",
        "description": "Manage subscription, billing, and payment methods",
        "category": "org",
        "scope": "org",
    },
    {
        "name": "org.deactivate",
        "description": "Deactivate the entire organization",
        "category": "org",
        "scope": "org",
    },
]


# ── Seeders ────────────────────────────────────────────────────────────────────

async def seed_roles(session: AsyncSession) -> None:
    """
    Insert missing system roles. Existing rows are never modified.

    System roles have is_system=True, organization_id=NULL, branch_id=NULL.
    The DB CheckConstraint (ck_role_system_org_consistent) enforces this.
    """
    inserted = 0
    skipped = 0

    for entry in SYSTEM_ROLES:
        result = await session.execute(
            select(Role).where(
                Role.name == entry["name"],
                Role.is_system == True,  # noqa: E712
            )
        )
        if result.scalar_one_or_none():
            skipped += 1
            continue

        session.add(Role(
            name=entry["name"],
            description=entry["description"],
            is_system=True,
            organization_id=None,
            branch_id=None,
        ))
        inserted += 1

    await session.commit()
    logger.info(
        "Role seed complete — inserted: %d, skipped (already exist): %d",
        inserted,
        skipped,
    )
    print(f"✓ Roles:       inserted {inserted}, skipped {skipped} existing.")


async def seed_permissions(session: AsyncSession) -> None:
    """Insert missing permissions. Existing rows are never modified."""
    inserted = 0
    skipped = 0

    for entry in PERMISSIONS:
        result = await session.execute(
            select(Permission).where(Permission.name == entry["name"])
        )
        if result.scalar_one_or_none():
            skipped += 1
            continue

        session.add(Permission(
            name=entry["name"],
            description=entry["description"],
            category=entry["category"],
            scope=entry.get("scope", "both"),
        ))
        inserted += 1

    await session.commit()
    logger.info(
        "Permission seed complete — inserted: %d, skipped (already exist): %d",
        inserted,
        skipped,
    )
    print(f"✓ Permissions: inserted {inserted}, skipped {skipped} existing.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async for session in get_db():
        # Roles first — signup_service.get_system_role() depends on them
        await seed_roles(session)
        await seed_permissions(session)
        break


if __name__ == "__main__":
    asyncio.run(main())