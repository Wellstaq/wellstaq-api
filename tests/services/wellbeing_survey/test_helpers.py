"""Tests for wellbeing-survey window helpers."""

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing_survey import WellbeingSurveyWindow
from app.services.wellbeing_survey._helpers import (
    compute_window_range,
    get_open_window,
    get_open_window_or_404,
)
from tests.conftest import make_org_with_owner


@pytest.mark.parametrize(
    ("cadence", "expected_end"),
    [
        ("weekly", date(2026, 9, 7)),
        ("biweekly", date(2026, 9, 14)),
        ("monthly", date(2026, 9, 30)),
    ],
)
def test_compute_window_range(cadence: str, expected_end: date):
    assert compute_window_range(cadence=cadence, start=date(2026, 9, 1)) == (
        date(2026, 9, 1),
        expected_end,
    )


def test_compute_window_range_rejects_unknown_cadence():
    with pytest.raises(KeyError):
        compute_window_range(cadence="daily", start=date(2026, 9, 1))


@pytest.mark.asyncio
async def test_get_open_window_returns_latest_open_window(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    older = WellbeingSurveyWindow(
        organization_id=org.id,
        window_start=date(2026, 8, 1),
        window_end=date(2026, 8, 31),
    )
    latest = WellbeingSurveyWindow(
        organization_id=org.id,
        window_start=date(2026, 9, 1),
        window_end=date(2026, 9, 30),
    )
    db_session.add_all([older, latest])
    await db_session.flush()

    assert await get_open_window(session=db_session, organization_id=org.id) is latest
    assert await get_open_window_or_404(session=db_session, organization_id=org.id) is latest


@pytest.mark.asyncio
async def test_get_open_window_ignores_closed_and_other_tenants(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    closed = WellbeingSurveyWindow(
        organization_id=org.id,
        window_start=date(2026, 9, 1),
        window_end=date(2026, 9, 30),
        is_closed=True,
    )
    db_session.add(closed)
    await db_session.flush()

    assert await get_open_window(session=db_session, organization_id=org.id) is None
    with pytest.raises(HTTPException) as exc:
        await get_open_window_or_404(session=db_session, organization_id=org.id)
    assert exc.value.status_code == 404
