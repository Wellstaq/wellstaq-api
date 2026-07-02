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
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Organization

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
      409 — a branch with this name already exists in the org
             (case-insensitive; the DB has a UNIQUE constraint too, this
             gives a friendlier error before hitting it).
    """
    name = name.strip()

    # ── Org must exist and be active ────────────────────────────────────────
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    if org_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    # ── Name uniqueness (case-insensitive) within the org ──────────────────
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