"""
app/services/engagement/checkin_service.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyCheckin
from app.models.activity import CHECKIN_VOCABULARY

WAT_OFFSET = timedelta(hours=1)  # placeholder — see note below


def _today() -> date:
    # NOTE: matches the model's own documented simplification — derived
    # from UTC server time, not the user's local calendar day. Same
    # known limitation flagged in DailyCheckin's docstring; revisit once
    # per-user timezone storage exists. Kept consistent with that note
    # rather than silently fixing only the checkin submission path while
    # the challenge progress path (WAT-based) uses a different rule.
    return datetime.now(timezone.utc).date()


async def submit_checkin(
    *, session: AsyncSession, user_id: str, mood: str, energy_level: str, stress_level: str, notes: str | None
) -> DailyCheckin:
    for field_name, value in (("mood", mood), ("energy_level", energy_level), ("stress_level", stress_level)):
        if value not in CHECKIN_VOCABULARY:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Invalid {field_name} '{value}' — must be one of {CHECKIN_VOCABULARY}.",
            )

    today = _today()

    existing = await session.execute(
        select(DailyCheckin).where(DailyCheckin.user_id == user_id, DailyCheckin.checkin_date == today)
    )
    checkin = existing.scalar_one_or_none()

    if checkin is not None:
        # Already checked in today — update in place rather than reject,
        # so a user correcting an earlier submission doesn't need a
        # separate edit endpoint.
        checkin.mood = mood
        checkin.energy_level = energy_level
        checkin.stress_level = stress_level
        checkin.notes = notes
        checkin.checked_in_at = datetime.now(timezone.utc)
    else:
        checkin = DailyCheckin(
            user_id=user_id, mood=mood, energy_level=energy_level, stress_level=stress_level,
            notes=notes, checked_in_at=datetime.now(timezone.utc), checkin_date=today,
        )
        session.add(checkin)

    await session.flush()
    await session.refresh(checkin)
    return checkin


async def get_checkins(
    *, session: AsyncSession, user_id: str, offset: int = 0, limit: int = 50
) -> tuple[list[DailyCheckin], int]:
    query = (
        select(DailyCheckin)
        .where(DailyCheckin.user_id == user_id)
        .order_by(DailyCheckin.checkin_date.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(DailyCheckin).where(DailyCheckin.user_id == user_id)

    total = (await session.execute(count_query)).scalar_one()
    checkins = (await session.execute(query)).scalars().all()
    return list(checkins), total


async def get_streak(*, session: AsyncSession, user_id: str) -> tuple[int, bool]:
    """
    Counts consecutive days ending today (or yesterday, if today's
    check-in hasn't happened yet — a missed-today check-in shouldn't
    zero out an otherwise-intact streak until the day fully passes).

    Walks backward day by day rather than a single query, since SQLite
    (dev/test) has no clean "detect consecutive dates" window-function
    idiom that's portable to Postgres without dialect-specific SQL —
    this keeps the logic identical on both. Fine at this scale (one
    user's check-ins, bounded by how many days back a streak can
    realistically run); revisit only if streak queries become a
    measured hot path.
    """
    result = await session.execute(
        select(DailyCheckin.checkin_date).where(DailyCheckin.user_id == user_id).order_by(DailyCheckin.checkin_date.desc())
    )
    dates = {row[0] for row in result.all()}

    today = _today()
    checked_in_today = today in dates

    streak = 0
    cursor = today if checked_in_today else today - timedelta(days=1)
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)

    return streak, checked_in_today