"""Aggregate the four signals shown by the dashboard trend card."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ANONYMITY_THRESHOLD
from app.models import DailyCheckin, EngagementLog, User, WellbeingScore
from app.models.wellbeing import WellbeingDimension

TrendPeriod = Literal["week", "month", "three_months"]
_PERIOD_DAYS: dict[TrendPeriod, int] = {"week": 7, "month": 30, "three_months": 90}
_ENERGY_SCORES = {"good": 100.0, "tired": 60.0, "stressed": 40.0, "bad": 20.0}
_STRESS_SCORES = {level: 100.0 - score for level, score in _ENERGY_SCORES.items()}


def _average_or_none(values: list[float]) -> float | None:
    if len(values) < ANONYMITY_THRESHOLD:
        return None
    return round(sum(values) / len(values), 2)


async def get_engagement_wellbeing_trends(
    *, session: AsyncSession, organization_id: str, branch_id: str | None, period: TrendPeriod,
) -> dict:
    """Combine check-ins and job outputs as anonymity-safe 0-100 aggregates."""
    end_date = date.today()
    start_date = end_date - timedelta(days=_PERIOD_DAYS[period] - 1)
    values: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    checkins = (
        select(DailyCheckin.checkin_date, DailyCheckin.energy_level, DailyCheckin.stress_level)
        .join(User, User.id == DailyCheckin.user_id)
        .where(User.organization_id == organization_id, DailyCheckin.checkin_date.between(start_date, end_date))
    )
    if branch_id is not None:
        checkins = checkins.where(User.branch_id == branch_id)
    for point_date, energy, stress in (await session.execute(checkins)).all():
        if energy in _ENERGY_SCORES:
            values[point_date]["energy_level"].append(_ENERGY_SCORES[energy])
        if stress in _STRESS_SCORES:
            values[point_date]["stress_level"].append(_STRESS_SCORES[stress])

    social = (
        select(WellbeingScore.period, WellbeingScore.score)
        .join(WellbeingDimension, WellbeingDimension.id == WellbeingScore.dimension_id)
        .join(User, User.id == WellbeingScore.user_id)
        .where(
            WellbeingScore.organization_id == organization_id,
            WellbeingDimension.name == "Social",
            WellbeingScore.period.between(start_date.isoformat(), end_date.isoformat()),
        )
    )
    if branch_id is not None:
        social = social.where(User.branch_id == branch_id)
    for period_value, score in (await session.execute(social)).all():
        try:
            point_date = date.fromisoformat(period_value)
        except (TypeError, ValueError):
            continue
        values[point_date]["social_interaction"].append(float(score))

    productivity = select(EngagementLog.date, EngagementLog.value).where(
        EngagementLog.organization_id == organization_id,
        EngagementLog.metric_type == "productivity",
        EngagementLog.date.between(start_date, end_date),
    )
    if branch_id is not None:
        productivity = productivity.where(EngagementLog.branch_id == branch_id)
    for point_date, score in (await session.execute(productivity)).all():
        values[point_date]["productivity"].append(float(score))

    points = []
    for offset in range(_PERIOD_DAYS[period]):
        point_date = start_date + timedelta(days=offset)
        point_values = values[point_date]
        points.append({
            "date": point_date,
            "stress_level": _average_or_none(point_values["stress_level"]),
            "energy_level": _average_or_none(point_values["energy_level"]),
            "social_interaction": _average_or_none(point_values["social_interaction"]),
            "productivity": _average_or_none(point_values["productivity"]),
        })

    return {"period": period, "start_date": start_date, "end_date": end_date, "points": points}
