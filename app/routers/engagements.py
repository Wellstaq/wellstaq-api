"""
app/routers/engagement.py

Fully personal, self-only — same access model as notifications. No
permission checks, no branch/org scoping beyond identifying the caller.
A user only ever sets/views their own priorities and check-ins.

Routes:
  PUT /engagement/priorities         — replace my priority list
  GET /engagement/priorities         — list my priorities

  POST /engagement/checkins          — submit (or update) today's check-in
  GET  /engagement/checkins          — my check-in history
  GET  /engagement/checkins/streak   — my current streak
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.engagement import (
    CheckinListResponse,
    CheckinResponse,
    CheckinStreakResponse,
    CheckinSubmitRequest,
    PriorityListResponse,
    PriorityResponse,
    PrioritySetRequest,
)
from app.services.engagement import get_checkins, get_priorities, get_streak, set_priorities, submit_checkin

router = APIRouter(prefix="/engagement", tags=["engagement"])


@router.put("/priorities", response_model=PriorityListResponse, status_code=status.HTTP_200_OK)
async def set_priorities_route(
    body: PrioritySetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PriorityListResponse:
    async with request_guard(db, "/engagement/priorities"):
        priorities = await set_priorities(session=db, user_id=current_user.user_id, priorities=body.priorities)
        await db.commit()
        return PriorityListResponse(items=[PriorityResponse.model_validate(p) for p in priorities])


@router.get("/priorities", response_model=PriorityListResponse, status_code=status.HTTP_200_OK)
async def list_priorities_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PriorityListResponse:
    async with request_guard(db, "/engagement/priorities"):
        priorities = await get_priorities(session=db, user_id=current_user.user_id)
        return PriorityListResponse(items=[PriorityResponse.model_validate(p) for p in priorities])


@router.post(
    "/checkins", response_model=CheckinResponse, status_code=status.HTTP_201_CREATED,
    description="Submits today's check-in. Calling this again the same day updates the existing entry instead of creating a duplicate.",
)
async def submit_checkin_route(
    body: CheckinSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CheckinResponse:
    async with request_guard(db, "/engagement/checkins"):
        checkin = await submit_checkin(
            session=db, user_id=current_user.user_id, mood=body.mood,
            energy_level=body.energy_level, stress_level=body.stress_level, notes=body.notes,
        )
        await db.commit()
        return CheckinResponse.model_validate(checkin)


@router.get("/checkins", response_model=CheckinListResponse, status_code=status.HTTP_200_OK)
async def list_checkins_route(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CheckinListResponse:
    async with request_guard(db, "/engagement/checkins"):
        checkins, total = await get_checkins(session=db, user_id=current_user.user_id, offset=offset, limit=limit)
        return CheckinListResponse(
            items=[CheckinResponse.model_validate(c) for c in checkins], total=total, offset=offset, limit=limit,
        )


@router.get("/checkins/streak", response_model=CheckinStreakResponse, status_code=status.HTTP_200_OK)
async def checkin_streak_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CheckinStreakResponse:
    async with request_guard(db, "/engagement/checkins/streak"):
        streak, checked_in_today = await get_streak(session=db, user_id=current_user.user_id)
        return CheckinStreakResponse(current_streak_days=streak, checked_in_today=checked_in_today)