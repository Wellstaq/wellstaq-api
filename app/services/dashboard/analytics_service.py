from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ANONYMITY_THRESHOLD
from app.models import EngagementLog, User, WellbeingDimension, WellbeingPulseSnapshot, WellbeingScore

PERIOD_DAYS = {"week": 7, "month": 30, "three_months": 91}


def _date_bounds(period: str) -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=PERIOD_DAYS[period] - 1), end


async def get_dashboard_trends(*, session: AsyncSession, organization_id: str, branch_id: str | None, period: str) -> dict:
    start_date, end_date = _date_bounds(period)
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
    pulse_rows = list((await session.execute(
        select(WellbeingPulseSnapshot).where(
            WellbeingPulseSnapshot.organization_id == organization_id,
            WellbeingPulseSnapshot.branch_id == branch_id,
            WellbeingPulseSnapshot.computed_at >= start_dt,
            WellbeingPulseSnapshot.computed_at <= end_dt,
        ).order_by(WellbeingPulseSnapshot.computed_at.asc())
    )).scalars().all())

    productivity_filters = [
        EngagementLog.organization_id == organization_id,
        EngagementLog.metric_type == "productivity",
        EngagementLog.date >= start_date,
        EngagementLog.date <= end_date,
    ]
    if branch_id is not None:
        productivity_filters.append(EngagementLog.branch_id == branch_id)
    productivity_rows = (await session.execute(
        select(EngagementLog.date, func.avg(EngagementLog.value))
        .where(*productivity_filters).group_by(EngagementLog.date).order_by(EngagementLog.date.asc())
    )).all()

    def pulse_points(field: str, *, invert: bool = False) -> list[dict]:
        points = []
        for row in pulse_rows:
            raw = getattr(row, field)
            if raw is None:
                continue
            value = float(raw)
            points.append({"date": row.computed_at.date(), "value": round(100 - value if invert else value, 2)})
        return points

    return {
        "period": period, "scope": "branch" if branch_id else "organization", "branch_id": branch_id,
        "start_date": start_date, "end_date": end_date,
        "series": {
            "stress_level": pulse_points("stress_manageability_pct", invert=True),
            "energy_level": pulse_points("energy_recovery_pct"),
            "social_interaction": pulse_points("connection_belonging_pct"),
            "productivity": [
                {"date": point_date, "value": round(float(value), 2)}
                for point_date, value in productivity_rows if value is not None
            ],
        },
    }


async def get_wellbeing_distribution(*, session: AsyncSession, organization_id: str, branch_id: str | None) -> dict:
    period_query = select(func.max(WellbeingScore.period)).where(WellbeingScore.organization_id == organization_id)
    if branch_id is not None:
        period_query = period_query.join(User, User.id == WellbeingScore.user_id).where(User.branch_id == branch_id)
    latest_period = (await session.execute(period_query)).scalar_one_or_none()
    dimensions = list((await session.execute(select(WellbeingDimension).order_by(WellbeingDimension.name.asc()))).scalars().all())

    aggregates: dict[str, tuple[float | None, int]] = {}
    if latest_period is not None:
        filters = [WellbeingScore.organization_id == organization_id, WellbeingScore.period == latest_period]
        query = select(
            WellbeingScore.dimension_id, func.avg(WellbeingScore.score),
            func.count(func.distinct(WellbeingScore.user_id)),
        )
        if branch_id is not None:
            query = query.join(User, User.id == WellbeingScore.user_id)
            filters.append(User.branch_id == branch_id)
        rows = (await session.execute(query.where(*filters).group_by(WellbeingScore.dimension_id))).all()
        aggregates = {
            dimension_id: (float(average) if average is not None else None, int(count or 0))
            for dimension_id, average, count in rows
        }

    items = []
    for dimension in dimensions:
        average, count = aggregates.get(dimension.id, (None, 0))
        suppressed = 0 < count < ANONYMITY_THRESHOLD
        items.append({
            "dimension_id": dimension.id, "key": dimension.name.strip().lower().replace(" ", "_"),
            "label": dimension.name, "value": None if suppressed else (round(average, 2) if average is not None else None),
            "participant_count": count, "suppressed": suppressed,
        })

    computed_at = None
    if latest_period:
        computed_at = datetime.combine(date.fromisoformat(latest_period), datetime.min.time(), tzinfo=timezone.utc)
    return {
        "scope": "branch" if branch_id else "organization", "branch_id": branch_id,
        "period": latest_period, "computed_at": computed_at, "dimensions": items,
    }
