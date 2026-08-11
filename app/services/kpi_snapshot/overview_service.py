from __future__ import annotations

from calendar import month_abbr
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ANONYMITY_THRESHOLD
from app.models.challenge import ChallengeParticipant
from app.models.branch import Branch
from app.models.department import Department, DepartmentRankSnapshot
from app.models.engagement import ActivityLog
from app.models import KPISnapshot
from app.models.user import User
from app.models.wellbeing import WellbeingScore
from app.services.target import get_monthly_step_targets

PERIOD_DAYS = {"month": 30, "three_months": 90, "six_months": 180, "nine_months": 270, "year": 365}


def _change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if previous == 0:
        return None if current == 0 else 100.0
    return round(((current - previous) / previous) * 100, 1)


async def get_kpi_overview(*, session: AsyncSession, organization_id: str, period: str, branch_id: str | None) -> dict:
    today = date.today()
    days = PERIOD_DAYS[period]
    start = today - timedelta(days=days - 1)
    previous_start, previous_end = start - timedelta(days=days), start - timedelta(days=1)

    # ── Summary: average daily steps + active employees ──────────────────
    # Read from persisted daily KPI rows so both the current value and
    # comparable-period trend use the same historical source of truth.
    current_metrics = await _steps_and_active_totals(
        session=session, organization_id=organization_id, branch_id=branch_id, start=start, end=today,
    )
    current_steps, current_active = current_metrics or (None, 0)
    previous_metrics = await _steps_and_active_totals(
        session=session, organization_id=organization_id, branch_id=branch_id, start=previous_start, end=previous_end,
    )
    previous_steps, previous_active = previous_metrics or (None, 0)

    user_filters = [User.organization_id == organization_id]
    if branch_id:
        user_filters.append(User.branch_id == branch_id)
    total_users = int((await session.scalar(select(func.count(User.id)).where(*user_filters, User.status == "active"))) or 0)

    current_active_pct = round(current_active * 100 / total_users, 1) if total_users else None
    previous_active_pct = round(previous_active * 100 / total_users, 1) if total_users else None

    # ── Challenges won ────────────────────────────────────────────────────
    challenge_query = select(ChallengeParticipant.completed_at, User.branch_id).join(User, User.id == ChallengeParticipant.user_id).where(
        ChallengeParticipant.organization_id == organization_id,
        ChallengeParticipant.completed_at.is_not(None),
        ChallengeParticipant.completed_at >= datetime.combine(previous_start, datetime.min.time()),
    )
    if branch_id:
        challenge_query = challenge_query.where(User.branch_id == branch_id)
    completions = (await session.execute(challenge_query)).all()
    current_wins = sum(1 for completed, _ in completions if completed.date() >= start)
    previous_wins = sum(1 for completed, _ in completions if previous_start <= completed.date() <= previous_end)

    # ── Wellbeing / health score ──────────────────────────────────────────
    # WellbeingScore.period is stored as an ISO date string ("2026-08-01"),
    # which sorts identically as a string and as a date — so this filter
    # is safe and correct, and stops pulling the org's ENTIRE wellbeing
    # history on every request (the previous version had no date filter
    # at all here).
    score_query = select(WellbeingScore.score, WellbeingScore.period, WellbeingScore.user_id).join(User, User.id == WellbeingScore.user_id).where(
        WellbeingScore.organization_id == organization_id,
        WellbeingScore.period >= previous_start.isoformat(),
        WellbeingScore.period <= today.isoformat(),
    )
    if branch_id:
        score_query = score_query.where(User.branch_id == branch_id)
    score_rows = (await session.execute(score_query)).all()
    parsed_scores = []
    for score, score_period, user_id in score_rows:
        try:
            score_date = date.fromisoformat(score_period)
        except (TypeError, ValueError):
            continue
        parsed_scores.append((float(score), score_date, user_id))

    current_score_rows = [r for r in parsed_scores if r[1] == max((x[1] for x in parsed_scores if x[1] <= today), default=date.min)]
    previous_score_rows = [r for r in parsed_scores if previous_start <= r[1] <= previous_end]
    if previous_score_rows:
        previous_latest = max(r[1] for r in previous_score_rows)
        previous_score_rows = [r for r in previous_score_rows if r[1] == previous_latest]
    def per_user_averages(rows: list[tuple[float, date, str]]) -> list[float]:
        scores_by_user: dict[str, list[float]] = defaultdict(list)
        for score, _, user_id in rows:
            scores_by_user[user_id].append(score)
        return [sum(scores) / len(scores) for scores in scores_by_user.values()]

    current_scores = per_user_averages(current_score_rows)
    previous_scores = per_user_averages(previous_score_rows)
    current_people = len(current_scores)
    previous_people = len(previous_scores)
    health = round(sum(current_scores) / len(current_scores), 1) if current_people >= ANONYMITY_THRESHOLD else None
    previous_health = round(sum(previous_scores) / len(previous_scores), 1) if previous_people >= ANONYMITY_THRESHOLD else None

    # ── Monthly / weekly charts — read from precomputed daily snapshots ──
    # (KPISnapshot, written nightly by compute_daily_metric_snapshots)
    # instead of scanning raw per-user rows across the whole period.
    # Fixed chart windows: the summary dropdown must not truncate charts
    # whose own labels already define their time grain.
    month_index = today.year * 12 + today.month - 1 - 11
    monthly_chart_start = date(month_index // 12, month_index % 12 + 1, 1)
    weekly_chart_start = today - timedelta(days=today.weekday(), weeks=8)

    snapshot_query = select(KPISnapshot.period_start, func.sum(KPISnapshot.value)).where(
        KPISnapshot.organization_id == organization_id,
        KPISnapshot.metric_name == "steps_total",
        KPISnapshot.period_start >= min(monthly_chart_start, weekly_chart_start),
        KPISnapshot.period_start <= today,
    )
    if branch_id:
        snapshot_query = snapshot_query.where(KPISnapshot.branch_id == branch_id)
    snapshot_query = snapshot_query.group_by(KPISnapshot.period_start).order_by(KPISnapshot.period_start.asc())
    snapshot_rows = (await session.execute(snapshot_query)).all()

    by_month: dict[tuple[int, int], float] = defaultdict(float)
    by_week: dict[date, float] = defaultdict(float)
    for snap_date, total_value in snapshot_rows:
        by_month[(snap_date.year, snap_date.month)] += float(total_value)
        week_start = snap_date - timedelta(days=snap_date.weekday())
        by_week[week_start] += float(total_value)

    # ── Targets ────────────────────────────────────────────────────────────
    targets = await get_monthly_step_targets(
        session=session, organization_id=organization_id, branch_id=branch_id,
        period_start=monthly_chart_start, period_end=today,
    )

    months = []
    cursor = monthly_chart_start
    while cursor <= today:
        key = (cursor.year, cursor.month)
        period_key = f"{cursor.year}-{cursor.month:02d}"
        months.append({"name": f"{month_abbr[cursor.month]} {str(cursor.year)[2:]}", "actual": round(by_month.get(key, 0), 1), "target": round(targets.get(period_key, 0), 1) or None})
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)

    week_cursor = weekly_chart_start
    weekly = []
    while week_cursor <= today:
        weekly.append({"name": week_cursor.strftime("%d %b"), "steps": round(by_week.get(week_cursor, 0), 1)})
        week_cursor += timedelta(days=7)

    # ── Health distribution ───────────────────────────────────────────────
    distribution = []
    if current_people >= ANONYMITY_THRESHOLD:
        buckets = [("Excellent", 80, "#22C55E"), ("Good", 65, "#F97316"), ("Fair", 50, "#FDBA74"), ("At Risk", -1, "#DC2626")]
        remaining = list(current_scores)
        for name, minimum, color in buckets:
            count = sum(1 for value in remaining if value >= minimum)
            remaining = [value for value in remaining if value < minimum]
            distribution.append({"name": name, "value": round(count * 100 / len(current_scores), 1), "color": color})

    # ── Department performance (already a precomputed snapshot read) ─────
    latest_rank_query = select(func.max(DepartmentRankSnapshot.period_end)).join(
        Department, Department.id == DepartmentRankSnapshot.department_id,
    ).where(Department.organization_id == organization_id)
    if branch_id:
        latest_rank_query = latest_rank_query.where(Department.branch_id == branch_id)
    latest_rank_subquery = latest_rank_query.scalar_subquery()

    dept_query = select(Department.name, Branch.name, DepartmentRankSnapshot.performance_score).join(
        DepartmentRankSnapshot, DepartmentRankSnapshot.department_id == Department.id,
    ).join(Branch, Branch.id == Department.branch_id).where(
        Department.organization_id == organization_id,
        DepartmentRankSnapshot.period_end == latest_rank_subquery,
    )
    if branch_id:
        dept_query = dept_query.where(Department.branch_id == branch_id)
    department_performance = [
        {
            "name": name,
            "branch_name": None if branch_id else branch_name,
            "engagement": float(score),
        }
        for name, branch_name, score in (await session.execute(dept_query)).all()
        if score is not None
    ]

    # ── Top performers — bounded to current calendar month, direct SQL ───
    # Always a small window (<=31 days) regardless of the requested
    # period, so this stays a direct query rather than going through the
    # snapshot table.
    month_start = today.replace(day=1)
    top_query = (
        select(ActivityLog.user_id, func.sum(ActivityLog.value).label("total"))
        .join(User, User.id == ActivityLog.user_id)
        .where(
            ActivityLog.organization_id == organization_id,
            ActivityLog.metric_type == "steps",
            ActivityLog.date >= month_start,
            ActivityLog.date <= today,
            User.status == "active",
            *([ActivityLog.branch_id == branch_id] if branch_id else []),
        )
        .group_by(ActivityLog.user_id)
        .order_by(func.sum(ActivityLog.value).desc())
        .limit(4)
    )
    top_rows = (await session.execute(top_query)).all()

    performers = []
    if top_rows:
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_([r[0] for r in top_rows])))).scalars().all()}
        maximum = float(top_rows[0][1]) or 1
        for rank, (user_id, steps) in enumerate(top_rows, 1):
            user = users.get(user_id)
            if user:
                performers.append({"user_id": user.id, "first_name": user.first_name, "last_name": user.last_name, "avatar_url": user.avatar_url, "steps": round(float(steps), 1), "score": round(float(steps) * 100 / maximum, 1), "rank": rank})

    return {
        "period": period, "scope": "branch" if branch_id else "organization", "branch_id": branch_id,
        "summary": {
            "average_daily_steps": {"value": current_steps, "change": _change(current_steps, previous_steps)},
            "health_score": {"value": health, "change": _change(health, previous_health)},
            "active_employees": {"value": current_active_pct, "change": _change(current_active_pct, previous_active_pct)},
            "challenges_won": {"value": float(current_wins), "change": float(current_wins - previous_wins)},
        },
        "monthly_steps": months, "health_distribution": distribution,
        "department_performance": department_performance, "weekly_activity": weekly,
        "top_performers": performers,
    }


async def _steps_and_active_totals(
    *, session: AsyncSession, organization_id: str, branch_id: str | None, start: date, end: date,
) -> tuple[float | None, int]:
    """Read persisted daily KPI history instead of recomputing activity joins."""
    filters = [
        KPISnapshot.organization_id == organization_id,
        KPISnapshot.metric_name.in_(("steps_total", "steps_active_users")),
        KPISnapshot.period_start >= start,
        KPISnapshot.period_start <= end,
    ]
    if branch_id:
        filters.append(KPISnapshot.branch_id == branch_id)

    result = await session.execute(
        select(
            KPISnapshot.metric_name,
            func.coalesce(func.sum(KPISnapshot.value), 0.0),
        )
        .where(*filters)
        .group_by(KPISnapshot.metric_name)
    )
    totals = {metric_name: float(value) for metric_name, value in result.all()}
    active_user_days = totals.get("steps_active_users", 0.0)
    average = round(totals.get("steps_total", 0.0) / active_user_days, 1) if active_user_days else None
    day_count = (end - start).days + 1
    average_active_users = round(active_user_days / day_count) if active_user_days else 0
    return average, average_active_users
