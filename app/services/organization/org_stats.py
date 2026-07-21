"""
app/services/organization/org_stats.py

Live-computed structural counts for an organization or branch — org
headcount/structure, not user engagement data. No snapshotting: these
are cheap COUNT(*) queries over already-indexed FK columns
(organization_id/branch_id), computed fresh on every request.

KPISnapshot was scrapped for this purpose — these figures don't need
historical snapshots, they're always "right now" numbers, same
treatment as Challenges Won / Your Activity This Week elsewhere in the
app.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Challenge, Club, Department, Event, Post, User


async def get_org_stats(*, session: AsyncSession, organization_id: str) -> dict:
    total_users = (await session.execute(
        select(func.count()).select_from(User).where(User.organization_id == organization_id)
    )).scalar_one()

    total_branches = (await session.execute(
        select(func.count()).select_from(Branch).where(Branch.organization_id == organization_id)
    )).scalar_one()

    total_departments = (await session.execute(
        select(func.count()).select_from(Department).where(Department.organization_id == organization_id)
    )).scalar_one()

    total_clubs = (await session.execute(
        select(func.count()).select_from(Club).where(Club.organization_id == organization_id)
    )).scalar_one()

    total_events = (await session.execute(
        select(func.count()).select_from(Event).where(Event.organization_id == organization_id)
    )).scalar_one()

    total_challenges = (await session.execute(
        select(func.count()).select_from(Challenge).where(Challenge.organization_id == organization_id)
    )).scalar_one()

    active_challenges = (await session.execute(
        select(func.count()).select_from(Challenge).where(
            Challenge.organization_id == organization_id, Challenge.status == "active",
        )
    )).scalar_one()

    total_posts = (await session.execute(
        select(func.count()).select_from(Post).where(Post.organization_id == organization_id)
    )).scalar_one()

    return {
        "total_users": total_users,
        "total_branches": total_branches,
        "total_departments": total_departments,
        "total_clubs": total_clubs,
        "total_events": total_events,
        "total_challenges": total_challenges,
        "active_challenges": active_challenges,
        "total_posts": total_posts,
    }


async def get_branch_stats(*, session: AsyncSession, organization_id: str, branch_id: str) -> dict:
    total_users = (await session.execute(
        select(func.count()).select_from(User).where(
            User.organization_id == organization_id, User.branch_id == branch_id,
        )
    )).scalar_one()

    total_departments = (await session.execute(
        select(func.count()).select_from(Department).where(
            Department.organization_id == organization_id, Department.branch_id == branch_id,
        )
    )).scalar_one()

    total_clubs = (await session.execute(
        select(func.count()).select_from(Club).where(
            Club.organization_id == organization_id, Club.branch_id == branch_id,
        )
    )).scalar_one()

    total_events = (await session.execute(
        select(func.count()).select_from(Event).where(
            Event.organization_id == organization_id, Event.branch_id == branch_id,
        )
    )).scalar_one()

    return {
        "total_users": total_users,
        "total_departments": total_departments,
        "total_clubs": total_clubs,
        "total_events": total_events,
    }