"""
scripts/create_super_admin.py

Bootstraps the very first platform admin. Needed because every other
way to create an AdminUser (POST /app-admin/admins/invite) requires
being logged in as an admin with admins.manage already — a chicken-
and-egg problem the first super_admin has no other way out of.

Every admin after this first one should go through the real invite
flow (Admin team page in the console, or POST /app-admin/admins/invite)
so the audit log correctly records who invited them — this script has
no actor to attribute to, so invited_by is left null.

Usage:
    python -m scripts.create_super_admin --email owner@wellstaq.com --password "..." --first-name Wellstaq --last-name Owner

If the email already exists, re-run with --reset-password to rotate
its password instead of failing — useful for recovering a locked-out
super_admin without touching the database by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import select

from app.auth.security import hash_password
from app.jobs.shared import get_job_session
from app.models import AdminUser


async def create_or_reset(*, email: str, password: str, first_name: str, last_name: str, reset_password: bool) -> None:
    email = email.lower().strip()
    async with get_job_session() as session:
        existing = (await session.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()

        if existing is not None and not reset_password:
            print(f"✗ An admin with email '{email}' already exists (role={existing.role}, status={existing.status}).")
            print("  Re-run with --reset-password to rotate their password instead.")
            return

        if existing is not None:
            existing.hashed_password = hash_password(password)
            existing.status = "active"
            await session.commit()
            print(f"✓ Password reset for existing admin '{email}'.")
            return

        admin = AdminUser(
            email=email, hashed_password=hash_password(password),
            first_name=first_name, last_name=last_name,
            role="super_admin", status="active",
        )
        session.add(admin)
        await session.commit()
        print(f"✓ Created super_admin '{email}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default=None, help="Omit to be prompted securely instead of passing it on the command line.")
    parser.add_argument("--first-name", default="Wellstaq")
    parser.add_argument("--last-name", default="Owner")
    parser.add_argument("--reset-password", action="store_true", help="If the email already exists, rotate its password instead of failing.")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")

    asyncio.run(create_or_reset(
        email=args.email, password=password, first_name=args.first_name, last_name=args.last_name,
        reset_password=args.reset_password,
    ))


if __name__ == "__main__":
    main()
