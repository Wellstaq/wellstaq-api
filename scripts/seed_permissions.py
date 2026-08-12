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

Transaction contract
─────────────────────
seed_roles() and seed_permissions() only flush — they never commit.
This matches the rest of the codebase's convention ("services never
commit — the caller owns the transaction boundary"), and matters
because these two functions are also called from inside
signup_service's self-heal path using a caller-supplied session that
may belong to a request or a test. main() below is the one place that
owns commit, since it's the actual standalone-script entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.shared import get_job_session
from app.models.permission import Permission, Role, RolePermission

logger = logging.getLogger(__name__)


# ── System role catalogue ──────────────────────────────────────────────────────
#
# System roles are platform-owned:
#   is_system=True, organization_id=NULL, branch_id=NULL
#
# Uniqueness fields:
#   is_unique    — whether only one active holder is allowed within unique_scope
#   unique_scope — "branch" or "department" only (NULL when is_unique=False).
#                  There is NO "org" value anymore — the org-wide scope
#                  tier was removed entirely from the Role model. See
#                  permission.py's ck_role_unique_scope_valid.
#
#     "branch"     → one active holder per branch. Each branch is independent —
#                    conflict check is scoped to the target branch only.
#                    Multiple branches can each have their own holder.
#
#     "department" → one active holder per department. A branch can have
#                    multiple holders if it has multiple departments.
#                    Conflict check is scoped to the target department only.
#
#   super_admin is a special case: it is one-per-organization, but that
#   is enforced STRUCTURALLY via Organization.owner_id (exactly one
#   owner per org), not via is_unique/unique_scope or via
#   UserRoleAssignment — there is no UserRoleAssignment row ever
#   created for super_admin. See signup_service.py.
#
# RoleName constants (from app.core.constants) must match these names exactly.

SYSTEM_ROLES: list[dict] = [
    {
        "name": "super_admin",
        "description": (
            "Organization owner. Receives all platform permissions at registration. "
            "Has full authority across the entire org."
        ),
        # No unique_scope="org" — that value no longer exists in the
        # data model. One-per-org is enforced structurally via
        # Organization.owner_id instead, not via this table.
        "is_unique": False,
        "unique_scope": None,
    },
    {
        "name": "admin",
        "description": (
            "Organization administrator. Permissions granted explicitly by the super admin."
        ),
        "is_unique": False,
        "unique_scope": None,
    },
    {
        "name": "branch_manager",
        "description": (
            "Manages a specific branch. Permissions scoped to that branch, "
            "granted explicitly by an admin."
        ),
        "is_unique": True,
        "unique_scope": "branch",
    },
    {
        "name": "team_leader",
        "description": (
            "Leads a team within a department. Permissions granted explicitly "
            "by a branch manager or admin."
        ),
        "is_unique": True,
        "unique_scope": "department",
    },
    {
        "name": "member",
        "description": (
            "Standard employee. No permissions by default — "
            "granted individually by an admin."
        ),
        "is_unique": False,
        "unique_scope": None,
    },
]


# ── Permission catalogue ───────────────────────────────────────────────────────

PERMISSIONS: list[dict] = [
    {"name": "member.invite", "description": "Invite new users to the organization or a branch", "category": "member", "scope": "both"},
    {"name": "chat.moderate", "description": "Pin or unpin messages in a club or event group chat", "category": "member", "scope": "both"},
    {"name": "member.remove", "description": "Remove a user from the organization or a branch", "category": "member", "scope": "both"},
    {"name": "member.suspend", "description": "Suspend a user (set status=suspended)", "category": "member", "scope": "both"},
    {"name": "member.reinstate", "description": "Lift a suspension and restore a user to active status", "category": "member", "scope": "both"},
    {"name": "member.role.assign", "description": "Assign or change a user's role", "category": "member", "scope": "both"},
    {"name": "member.role.revoke", "description": "Revoke a user's current role", "category": "member", "scope": "both"},
    {"name": "member.department.assign", "description": "Move a user into or out of a department", "category": "member", "scope": "both"},
    {"name": "member.branch.assign", "description": "Transfer a user to a different branch", "category": "member", "scope": "both"},
    {"name": "member.permission.grant", "description": "Grant a permission to another user (subject to grant ceiling)", "category": "member", "scope": "both"},
    {"name": "member.permission.revoke", "description": "Revoke a permission from another user", "category": "member", "scope": "both"},
    {"name": "branch.create", "description": "Create a new branch within the organization", "category": "branch", "scope": "org"},
    {"name": "branch.update", "description": "Edit branch details (name, location, settings)", "category": "branch", "scope": "both"},
    {"name": "branch.delete", "description": "Delete a branch and all its data", "category": "branch", "scope": "org"},
    {"name": "branch.manager.assign", "description": "Designate or replace the manager of a branch", "category": "branch", "scope": "org"},
    {"name": "department.create", "description": "Create a new department within a branch", "category": "department", "scope": "both"},
    {"name": "department.update", "description": "Edit department details (name, description, rank)", "category": "department", "scope": "both"},
    {"name": "department.delete", "description": "Delete a department", "category": "department", "scope": "both"},
    {"name": "club.create", "description": "Create a new club within the org or a branch", "category": "club", "scope": "both"},
    {"name": "club.update", "description": "Edit club details", "category": "club", "scope": "both"},
    {"name": "club.delete", "description": "Delete a club", "category": "club", "scope": "both"},
    {"name": "club.member.remove", "description": "Remove a user from a club", "category": "club", "scope": "both"},
    {"name": "event.create", "description": "Create an org or branch event", "category": "event", "scope": "both"},
    {"name": "event.update", "description": "Edit an existing event", "category": "event", "scope": "both"},
    {"name": "event.delete", "description": "Delete an event", "category": "event", "scope": "both"},
    {"name": "challenge.create", "description": "Create a wellness or fitness challenge", "category": "challenge", "scope": "both"},
    {"name": "challenge.update", "description": "Edit an existing challenge", "category": "challenge", "scope": "both"},
    {"name": "challenge.delete", "description": "Delete a challenge", "category": "challenge", "scope": "both"},
    {"name": "post.delete", "description": "Permanently delete any post within scope", "category": "content", "scope": "both"},
    {"name": "post.moderate", "description": "Pin, hide, or flag posts without deleting them", "category": "content", "scope": "both"},
    {"name": "post.view_all", "description": "view all post across the organization including branch posts", "category": "content", "scope": "both"},
    {"name": "story.delete", "description": "Delete any story within scope", "category": "content", "scope": "both"},
    {"name": "comment.delete", "description": "Delete any comment within scope", "category": "content", "scope": "both"},
    {"name": "wellbeing.view_team", "description": "View aggregated wellbeing scores for a team or branch", "category": "wellbeing", "scope": "both"},
    {"name": "wellbeing.export", "description": "Export wellbeing reports to CSV or PDF", "category": "wellbeing", "scope": "both"},
    {"name": "overview", "description": "overview a branch or the org entirely", "category": "org", "scope": "both"},
    {"name": "org.stats", "description": "Organization statistics(total users count, total department count etc)", "category": "org", "scope": "both"},
    {"name": "org.settings.update", "description": "Update organization-level settings and profile", "category": "org", "scope": "org"},
    {"name": "org.billing.manage", "description": "Manage subscription, billing, and payment methods", "category": "org", "scope": "org"},
    {"name": "org.deactivate", "description": "Deactivate the entire organization", "category": "org", "scope": "org"},


]


ADMIN_EXCLUSIONS = {"org.billing.manage", "org.deactivate", "org.settings.update"}
BRANCH_ADMIN_EXCLUSIONS = {
    "member.role.assign", "member.permission.grant", "branch.update",
    "member.permission.revoke", "member.role.revoke",
}
TEAM_LEADER_PERMISSIONS = {"overview", "wellbeing.view_team", "wellbeing.export"}


def system_role_permission_templates() -> dict[str, dict[str, str]]:
    all_permissions = {entry["name"]: entry["scope"] for entry in PERMISSIONS}
    return {
        "super_admin": {name: "org" for name in all_permissions},
        "admin": {name: "org" for name in all_permissions if name not in ADMIN_EXCLUSIONS},
        "branch_manager": {name: "branch" for name, catalogue_scope in all_permissions.items() if catalogue_scope == "both"},
        "team_leader": {name: "branch" for name in TEAM_LEADER_PERMISSIONS},
        "member": {},
    }


# ── Seeders ────────────────────────────────────────────────────────────────────

async def seed_roles(session: AsyncSession) -> None:
    """Insert missing system roles. Existing rows are never modified.
    Flushes only — does not commit."""
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
            is_unique=entry["is_unique"],
            unique_scope=entry["unique_scope"],
        ))
        inserted += 1

    await session.flush()
    logger.info(
        "Role seed complete — inserted: %d, skipped (already exist): %d",
        inserted, skipped,
    )
    print(f"✓ Roles:       inserted {inserted}, skipped {skipped} existing.")


async def seed_permissions(session: AsyncSession) -> None:
    """Insert missing permissions. Existing rows are never modified.
    Flushes only — does not commit."""
    inserted = 0
    skipped = 0

    for entry in PERMISSIONS:
        result = await session.execute(
            select(Permission).where(Permission.name == entry["name"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.description = entry["description"]
            existing.category = entry["category"]
            existing.scope = entry.get("scope", "both")
            skipped += 1
            continue

        session.add(Permission(
            name=entry["name"],
            description=entry["description"],
            category=entry["category"],
            scope=entry.get("scope", "both"),
        ))
        inserted += 1

    await session.flush()
    logger.info(
        "Permission seed complete — inserted: %d, skipped (already exist): %d",
        inserted, skipped,
    )
    print(f"✓ Permissions: inserted {inserted}, skipped {skipped} existing.")


async def seed_system_role_permissions(session: AsyncSession) -> None:
    """Synchronize immutable platform role templates to the catalogue."""
    roles = {
        role.name: role
        for role in (await session.execute(select(Role).where(Role.is_system == True))).scalars().all()  # noqa: E712
    }
    permissions = {
        permission.name: permission
        for permission in (await session.execute(select(Permission))).scalars().all()
    }
    templates = system_role_permission_templates()
    for role_name, desired in templates.items():
        role = roles.get(role_name)
        if role is None:
            continue
        existing = {
            row.permission_id: row
            for row in (await session.execute(select(RolePermission).where(RolePermission.role_id == role.id))).scalars().all()
        }
        desired_ids = {permissions[name].id for name in desired if name in permissions}
        for permission_id, row in existing.items():
            if permission_id not in desired_ids:
                await session.delete(row)
        for permission_name, scope in desired.items():
            permission = permissions.get(permission_name)
            if permission is None:
                continue
            row = existing.get(permission.id)
            if row is None:
                session.add(RolePermission(role_id=role.id, permission_id=permission.id, scope=scope))
            else:
                row.scope = scope
    await session.flush()
    print("✓ System role permission templates synchronized.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with get_job_session() as session:
        await seed_roles(session)
        await seed_permissions(session)
        await seed_system_role_permissions(session)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
