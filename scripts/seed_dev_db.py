"""
scripts/seed_dev_db.py

Dev-only seed script. Bypasses the service/router layer entirely and
inserts rows directly via the ORM.

WHAT IT DOES (in order):
  1. (Optional) DROP + CREATE all tables from your current models.
     Rebuilds the schema straight from Base.metadata every run, so it
     can never drift from your models the way a stale app.db file can.
  2. Reuse your REAL scripts/seed_permissions.py — calls its
     seed_roles() and seed_permissions() directly, so this script never
     maintains its own duplicate copy of the role/permission catalogue
     that could drift out of sync with the real one.
  3. Create an Organization.
  4. Look up the 5 SYSTEM roles (super_admin, admin, branch_manager,
     team_leader, member) that step 2 just seeded.
  5. Create the owner User with role=super_admin, and grant them ALL
     36 permissions org-wide (UserPermission rows, branch_id=None).
  6. Create a Branch and a Department.
  7. Create N additional test Users, each with a REQUIRED role from
     EXTRA_USERS config, assigned to the branch/department above.

Every user gets the SAME password (see PASSWORD below).

BEFORE RUNNING — fix these for your actual project layout:
  (a) DB engine import (search "ADJUST ME") — only needed for RESET_DB.
  (b) Password hashing function import (search "ADJUST ME").
  (c) Organization / UserPermission model field names, if they differ
      from what's assumed here (search "ADJUST ME").

Run with:
    python -m scripts.seed_dev_db
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_dev")


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

RESET_DB = True  # True = drop_all + create_all before seeding

PASSWORD = "TestPass123!"  # same password for every seeded user

ORG = {
    "business_name": "Wellstaq Test Org",
    "slug": "wellstaq-test-org",
    "employee_count": 10,
    "organization_type": "corporate",
    "work_model": "hybrid",
    "is_active": True,
}
OWNER = {
    "first_name": "Ada",
    "last_name": "Owner",
    "email": "owner@example.com",
    "phone_number": "+2348000000001",
}

BRANCH = {
    "name": "Head Office",
}

DEPARTMENT = {
    "name": "Engineering",
    "description": "Test department seeded by script",
    "avatar_initial": "EN",
}

# role must be one of: "admin", "branch_manager", "team_leader", "member"
# (super_admin is reserved for the org owner only — not assignable here)
EXTRA_USERS = [
    {"first_name": "Chidi", "last_name": "Admin", "email": "admin@example.com", "role": "admin"},
    {"first_name": "Tunde", "last_name": "Manager", "email": "manager@example.com", "role": "branch_manager"},
    {"first_name": "Bisi", "last_name": "Lead", "email": "lead@example.com", "role": "team_leader"},
    {"first_name": "Femi", "last_name": "Member", "email": "member1@example.com", "role": "member"},
    {"first_name": "Zainab", "last_name": "Member", "email": "member2@example.com", "role": "member"},
]


# ─────────────────────────────────────────────────────────────────────────────
# ADJUST ME (a) — only needed if RESET_DB=True. Point at your actual engine.
# ─────────────────────────────────────────────────────────────────────────────
from app.core.database import engine, get_db
from app.models.base import Base


# ─────────────────────────────────────────────────────────────────────────────
# ADJUST ME (b) — point at your real password hashing function so seeded
# users can actually log in through your real /auth/login.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from app.auth.security import hash_password  # noqa: E402
except ImportError:
    logger.warning(
        "Could not import app.core.security.hash_password — falling back to "
        "a local bcrypt hasher. If your login check uses a DIFFERENT hashing "
        "scheme, seeded users will NOT be able to log in. Fix the import above."
    )
    from passlib.context import CryptContext

    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def hash_password(raw: str) -> str:
        return _pwd_ctx.hash(raw)


from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.models import (  # noqa: E402
    Branch,
    Department,
    Organization,
    User,
    UserAuthProvider,
    UserPermission,
    UserSettings,
)
from app.models.permission import Permission, Role  # noqa: E402

# Reuse the REAL role/permission catalogue — single source of truth.
from scripts.seed_permissions import seed_roles, seed_permissions  # noqa: E402

ROLE_NAMES = ["super_admin", "admin", "branch_manager", "team_leader", "member"]


async def reset_schema() -> None:
    logger.info("Dropping and recreating all tables from current models...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema rebuilt.")


async def load_system_roles(session: AsyncSession) -> dict[str, Role]:
    """Fetch the system roles that seed_roles() just ensured exist."""
    result = await session.execute(
        select(Role).where(Role.name.in_(ROLE_NAMES), Role.is_system == True)  # noqa: E712
    )
    roles = {r.name: r for r in result.scalars().all()}
    missing = set(ROLE_NAMES) - set(roles)
    if missing:
        raise RuntimeError(
            f"System roles missing after seed_roles(): {missing}. "
            "Check ROLE_NAMES matches scripts/seed_permissions.py's SYSTEM_ROLES."
        )
    return roles


async def load_all_permissions(session: AsyncSession) -> list[Permission]:
    result = await session.execute(select(Permission))
    return list(result.scalars().all())


async def grant_all_permissions(
    session: AsyncSession,
    *,
    user: User,
    organization_id: str,
    permissions: list[Permission],
    granted_by: str,
) -> None:
    """Grant every permission to `user`, org-wide (branch_id=None)."""
    for perm in permissions:
        session.add(
            UserPermission(
                user_id=user.id,
                permission_id=perm.id,
                organization_id=organization_id,
                branch_id=None,
                granted_by=granted_by,
            )
        )
    await session.flush()
    logger.info("Granted all %d permissions to %s", len(permissions), user.email)


async def create_user_with_auth(
    session: AsyncSession,
    *,
    organization_id: str,
    branch_id: str | None,
    role_id: str,  # required — no user is created without a role
    first_name: str,
    last_name: str,
    email: str,
    phone_number: str | None = None,
    status: str = "active",
) -> User:
    """
    Creates a User + UserSettings + a 'password' UserAuthProvider row.
    role_id is required — enforced here even though the DB column is
    nullable, per the product rule that every user must have a role.
    """
    if not role_id:
        raise ValueError(f"role_id is required — got {role_id!r} for {email}")

    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email.lower().strip(),
        phone_number=phone_number,
        hashed_password=hash_password(PASSWORD),
        status=status,
        organization_id=organization_id,
        branch_id=branch_id,
        role_id=role_id,
    )
    session.add(user)
    await session.flush()

    session.add(UserSettings(user_id=user.id, organization_id=organization_id))
    session.add(
        UserAuthProvider(
            user_id=user.id,
            organization_id=organization_id,
            provider="password",
            provider_user_id=None,
            access_token=None,
            refresh_token=None,
        )
    )
    await session.flush()

    logger.info(
        "Created user %s <%s> role_id=%s (id=%s)",
        f"{first_name} {last_name}", email, role_id, user.id,
    )
    return user


async def seed() -> None:
    if RESET_DB:
        await reset_schema()

    async for session in get_db():
        # ── Reuse the real roles + permissions seeder ───────────────────────
        await seed_roles(session)
        await seed_permissions(session)

        roles = await load_system_roles(session)
        all_permissions = await load_all_permissions(session)

        # ── Organization ─────────────────────────────────────────────────────
        org = Organization(**ORG)
        session.add(org)
        await session.flush()
        logger.info("Created organization %s (id=%s)", org.business_name, org.id)

        # ── Owner (super_admin) ──────────────────────────────────────────────
        owner = await create_user_with_auth(
            session,
            organization_id=org.id,
            branch_id=None,
            role_id=roles["super_admin"].id,
            **OWNER,
        )

        if hasattr(org, "owner_id"):
            org.owner_id = owner.id
            await session.flush()

        await grant_all_permissions(
            session,
            user=owner,
            organization_id=org.id,
            permissions=all_permissions,
            granted_by=owner.id,
        )

        # ── Branch ───────────────────────────────────────────────────────────
        branch = Branch(organization_id=org.id, name=BRANCH["name"], created_by=owner.id)
        session.add(branch)
        await session.flush()
        logger.info("Created branch %s (id=%s)", branch.name, branch.id)

        # ── Department ───────────────────────────────────────────────────────
        department = Department(
            organization_id=org.id,
            branch_id=branch.id,
            name=DEPARTMENT["name"],
            description=DEPARTMENT.get("description"),
            avatar_initial=DEPARTMENT.get("avatar_initial"),
            created_by=owner.id,
        )
        session.add(department)
        await session.flush()
        logger.info("Created department %s (id=%s)", department.name, department.id)

        # ── Extra users — each with a required, non-super_admin role ───────────
        for u in EXTRA_USERS:
            role_key = u["role"]
            if role_key == "super_admin":
                raise ValueError(
                    f"{u['email']}: 'super_admin' is reserved for the org "
                    "owner and cannot be assigned via EXTRA_USERS."
                )
            if role_key not in roles:
                raise ValueError(f"{u['email']}: unknown role '{role_key}'")

            await create_user_with_auth(
                session,
                organization_id=org.id,
                branch_id=branch.id,
                role_id=roles[role_key].id,
                first_name=u["first_name"],
                last_name=u["last_name"],
                email=u["email"],
                phone_number=u.get("phone_number"),
            )

        await session.commit()

        logger.info("─" * 60)
        logger.info("Seed complete. All users share password: %s", PASSWORD)
        logger.info("  owner:  %s (super_admin, full permissions)", OWNER["email"])
        for u in EXTRA_USERS:
            logger.info("  %-16s %s", u["role"], u["email"])
        logger.info("  org_id:        %s", org.id)
        logger.info("  branch_id:     %s", branch.id)
        logger.info("  department_id: %s", department.id)
        logger.info("─" * 60)
        break


if __name__ == "__main__":
    asyncio.run(seed())