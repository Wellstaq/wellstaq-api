"""
app/services/challenge/get_challenge.py
"""

from __future__ import annotations

from app.models import Challenge
from app.services.challenge._helpers import get_challenge_or_404
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str) -> Challenge:
    return await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)


async def get_challenges(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    viewer_branch_id: str | None = None,
    is_super_admin: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Challenge], int]:
    from sqlalchemy import or_

    query = select(Challenge).where(Challenge.organization_id == organization_id)
    count_query = select(func.count()).select_from(Challenge).where(Challenge.organization_id == organization_id)

    if branch_id is not None:
        query = query.where(Challenge.branch_id == branch_id)
        count_query = count_query.where(Challenge.branch_id == branch_id)
    elif not is_super_admin:
        visibility = or_(Challenge.branch_id.is_(None), Challenge.branch_id == viewer_branch_id)
        query = query.where(visibility)
        count_query = count_query.where(visibility)

    query = query.order_by(Challenge.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    challenges = (await session.execute(query)).scalars().all()
    return list(challenges), total