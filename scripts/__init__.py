"""
scripts/seed_permissions.py

Platform permission seeder.

Run once during initial setup, and again whenever new permissions are added.
Existing rows are never touched — only missing ones are inserted.

Usage:
    python -m scripts.seed_permissions

Design:
    - Permissions are platform-owned. Orgs cannot create, rename, or delete them.
    - name follows "resource.action" convention, unique platform-wide.
    - category groups related permissions for UI display and future filtering.
    - scope column documents the INTENDED scope of each permission:
        "org"    → should only ever be granted org-wide (branch_id=NULL)
        "branch" → typically granted branch-scoped, but can be org-wide
        "both"   → can sensibly be granted at either scope
      This is advisory only — the DB does not enforce it. The service
      layer and admin UI should use it to guide grant flows.

Adding new permissions later:
    1. Append to PERMISSIONS below.
    2. Re-run this script (safe — existing rows are skipped).
    3. If in production, write an Alembic data migration that does the
       same INSERT ... WHERE NOT EXISTS so it runs automatically on deploy.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow running as `python -m scripts.seed_permissions` from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db  # adjust if your session factory differs
from app.models.permission import Permission

logger = logging.getLogger(__name__)

# ── Permission catalogue ───────────────────────────────────────────────────────
#
# Fields per entry:
#   name        — unique platform identifier, "resource.action"
#   description — human-readable label shown in admin UI
#   category    — grouping key for UI sections
#   scope       — advisory: "org" | "branch" | "both"
#
# Scope key:
#   org    → should only ever be granted org-wide (branch_id=NULL)
#            e.g. inviting someone into the org, deactivating the org
#   branch → typically granted scoped to a branch; can be org-wide
#            e.g. suspending a user within a branch
#   both   → sensibly granted at either level depending on context

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


# ── Seeder ─────────────────────────────────────────────────────────────────────

async def seed_permissions(session: AsyncSession) -> None:
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
        ))
        inserted += 1

    await session.commit()
    logger.info(
        "Permission seed complete — inserted: %d, skipped (already exist): %d",
        inserted,
        skipped,
    )
    print(f"✓ Inserted {inserted} permission(s). Skipped {skipped} existing.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # get_db is an async generator — pull one session from it
    async for session in get_db():
        await seed_permissions(session)
        break


if __name__ == "__main__":
    asyncio.run(main())