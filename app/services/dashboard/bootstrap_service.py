"""
app/services/dashboard/bootstrap_service.py

Composes a single "everything the dashboard needs on load" payload by
calling existing domain services — deliberately does NOT duplicate
query logic that already exists elsewhere (get_org_stats, leaderboard,
etc.), just assembles their outputs into one response.

participantOptions and activeBranch are intentionally omitted — the
former is deferred, the latter is frontend-only state, never sent by
the backend.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Department, DepartmentMember, User
from app.services.leaderboard import get_org_leaderboard
from app.services.organization.org_directory import get_organization_members
from app.services.auth.session_service import get_me


async def get_dashboard_bootstrap(
    *, session: AsyncSession, organization_id: str, user_id: str,
    role: str | None, branch_id: str | None, permissions: list,
    metric_type: str = "steps",
) -> dict:
    user, department_id = await get_me(session=session, user_id=user_id, role=role)
    settings = user.settings
    user_response = {
        "user_id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": role,
        "organization_id": organization_id,
        "branch_id": branch_id,
        "department_id": department_id,
        "location": user.country,
        "phone_number": user.phone_number,
        "status": user.status,
        "permissions": permissions,
        "avatar_url": user.avatar_url,
        "two_fa_enabled": settings.two_fa_enabled if settings else False,
        "two_fa_method": settings.two_fa_method if settings else None,
    }

    members, _total = await get_organization_members(session=session, organization_id=organization_id, offset=0, limit=200)

    branches_result = await session.execute(
        select(Branch).where(Branch.organization_id == organization_id).order_by(Branch.created_at.asc())
    )
    branches = list(branches_result.scalars().all())

    departments = await _build_department_bootstrap(session=session, organization_id=organization_id)

    from app.services.event import get_events
    event_rows, _ = await get_events(
        session=session, organization_id=organization_id, offset=0, limit=20,
    )
    # get_events returns (Event, participant_count) pairs.  Convert them to
    # response-shaped mappings before DashboardBootstrapResponse validation;
    # passing SQLAlchemy Row/tuple objects directly makes every EventResponse
    # field appear missing to Pydantic.
    events = [
        {**event.__dict__, "participant_count": participant_count}
        for event, participant_count in event_rows
    ]

    from app.services.challenge import get_challenges
    challenges, _ = await get_challenges(
        session=session, organization_id=organization_id, viewer_branch_id=None, is_super_admin=True, offset=0, limit=20,
    )

    leaderboard_rows, _period_start, _period_end = await get_org_leaderboard(
        session=session, organization_id=organization_id, metric_type=metric_type, period_type="weekly", limit=20,
    )

    return {
        "user": user_response,
        "branches": branches,
        "members": members,
        "departments": departments,
        "events": events,
        "challenges": challenges,
        "leaderboard": [
            {
                "user_id": user_row.id, "first_name": user_row.first_name, "last_name": user_row.last_name,
                "avatar_url": user_row.avatar_url, "value": snapshot.value, "rank": snapshot.rank,
                "previous_rank": snapshot.previous_rank, "org_rank": snapshot.org_rank,
                "previous_org_rank": snapshot.previous_org_rank,
            }
            for snapshot, user_row in leaderboard_rows
        ],
    }


async def _build_department_bootstrap(*, session: AsyncSession, organization_id: str) -> list[dict]:
    from app.models.department import DepartmentRankSnapshot

    dept_result = await session.execute(select(Department).where(Department.organization_id == organization_id))
    departments = dept_result.scalars().all()

    result = []
    for dept in departments:
        member_count_result = await session.execute(
            select(func.count()).select_from(DepartmentMember).where(DepartmentMember.department_id == dept.id)
        )
        member_count = member_count_result.scalar_one()

        avatars_result = await session.execute(
            select(User.avatar_url)
            .join(DepartmentMember, DepartmentMember.user_id == User.id)
            .where(DepartmentMember.department_id == dept.id, User.avatar_url.is_not(None))
            .limit(5)
        )
        avatars = [row[0] for row in avatars_result.all()]

        latest_snapshot_result = await session.execute(
            select(DepartmentRankSnapshot)
            .where(DepartmentRankSnapshot.department_id == dept.id)
            .order_by(DepartmentRankSnapshot.period_start.desc())
            .limit(1)
        )
        snapshot = latest_snapshot_result.scalar_one_or_none()

        result.append({
            "id": dept.id, "name": dept.name, "members": member_count,
            "activities": snapshot.total_activities if snapshot else None,
            "rank": snapshot.rank if snapshot else None,
            "branch": dept.branch_id, "avatars": avatars,
        })

    return result
