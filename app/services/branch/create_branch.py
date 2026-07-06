"""
app/services/branches/create_branch.py

Branch creation.

Contract:
  - Accepts a live AsyncSession.
  - Does not call commit() — the router owns that.
  - flush() populates the PK before the router serializes the response.

Manager assignment is intentionally NOT part of creation — a branch is
always created without a manager, and manager_id is set afterward via
the dedicated assign_branch_manager endpoint. This keeps manager_id
mutation on a single code path (see update_branch.py docstring).

Headquarters name reservation:
  "Headquarters" (case-insensitive) is reserved for the single
  auto-created branch every org gets at signup (Branch.is_headquarters=True).
  Allowing an admin to create a regular branch with that name would
  collide with the name uniqueness constraint, impersonate the protected
  branch semantically, and prevent the signup service from ever creating
  a real HQ branch for a re-used org. The guard below rejects it with
  a clear message before the uniqueness check runs.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Organization
from app.services.auth.signup_service import HEADQUARTERS_BRANCH_NAME

logger = logging.getLogger(__name__)


async def create_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    name: str,
    created_by: str,
) -> Branch:
    """
    Create a new branch under an organization. manager_id starts as
    None — assign one later via PUT /branches/{branch_id}/manager.

    Raises:
      404 — organization not found.
      403 — organization is deactivated.
      400 — name is reserved ("Headquarters" is auto-created at signup).
      409 — a branch with this name already exists in the org
             (case-insensitive).
    """
    name = name.strip()

    # ── Org must exist and be active ───────────────────────────────────────────
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is deactivated.",
        )

    # ── Reserved name guard ────────────────────────────────────────────────────
    # "Headquarters" is the auto-created protected branch every org gets at
    # signup. Blocking it here keeps the name stable as a system identifier
    # and prevents collisions with the real HQ branch (which would fail at
    # the unique constraint anyway, but this gives a clearer error message).
    if name.lower() == HEADQUARTERS_BRANCH_NAME.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f'"{HEADQUARTERS_BRANCH_NAME}" is a reserved branch name. '
                "It is created automatically when the organization is registered "
                "and cannot be used for a regular branch."
            ),
        )

    # ── Name uniqueness (case-insensitive) within the org ─────────────────────
    duplicate = await session.execute(
        select(Branch).where(
            Branch.organization_id == organization_id,
            func.lower(Branch.name) == name.lower(),
        )
    )
    if duplicate.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A branch named '{name}' already exists in this organization.",
        )

    branch = Branch(
        organization_id=organization_id,
        name=name,
        is_headquarters=False,
        created_by=created_by,
    )
    session.add(branch)
    await session.flush()
    await session.refresh(branch)

    logger.info(
        "Branch created",
        extra={"branch_id": branch.id, "org_id": organization_id, "created_by": created_by},
    )
    return branch