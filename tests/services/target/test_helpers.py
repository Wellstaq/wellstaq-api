"""Tests for target period and tenant-scope helpers."""

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot_target import Target
from app.services.target._helpers import (
    build_period_label,
    get_target_or_404,
    period_date_range,
    validate_branch,
)
from tests.conftest import make_org_with_owner


@pytest.mark.parametrize(
    ("period_type", "reference", "expected"),
    [
        ("daily", date(2026, 7, 25), "2026-07-25"),
        ("weekly", date(2026, 7, 25), "2026-W30"),
        ("monthly", date(2026, 7, 25), "2026-07"),
    ],
)
def test_build_period_label(period_type, reference, expected):
    assert build_period_label(period_type=period_type, reference_date=reference) == expected


@pytest.mark.parametrize(
    ("period_type", "period", "expected"),
    [
        ("daily", "2026-07-25", (date(2026, 7, 25), date(2026, 7, 25))),
        ("weekly", "2026-W30", (date(2026, 7, 20), date(2026, 7, 26))),
        ("monthly", "2026-02", (date(2026, 2, 1), date(2026, 2, 28))),
        ("monthly", "2026-12", (date(2026, 12, 1), date(2026, 12, 31))),
    ],
)
def test_period_date_range(period_type, period, expected):
    assert period_date_range(period_type=period_type, period=period) == expected


@pytest.mark.parametrize("function", [build_period_label, period_date_range])
def test_period_helpers_reject_unknown_period_type(function):
    kwargs = {"period_type": "quarterly"}
    kwargs["reference_date" if function is build_period_label else "period"] = (
        date(2026, 1, 1) if function is build_period_label else "2026-Q1"
    )
    with pytest.raises(ValueError, match="Invalid period_type"):
        function(**kwargs)


@pytest.mark.asyncio
async def test_validate_branch_enforces_organization_ownership(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    await validate_branch(session=db_session, organization_id=org.id, branch_id=owner.branch_id)

    with pytest.raises(HTTPException) as exc:
        await validate_branch(
            session=db_session,
            organization_id="00000000-0000-0000-0000-000000000000",
            branch_id=owner.branch_id,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_target_or_404_is_tenant_scoped(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    target = Target(
        organization_id=org.id,
        branch_id=owner.branch_id,
        metric_type="steps",
        period="2026-09",
        target_value=10000,
    )
    db_session.add(target)
    await db_session.flush()

    assert await get_target_or_404(
        session=db_session, organization_id=org.id, target_id=target.id
    ) is target
    with pytest.raises(HTTPException) as exc:
        await get_target_or_404(
            session=db_session,
            organization_id="00000000-0000-0000-0000-000000000000",
            target_id=target.id,
        )
    assert exc.value.status_code == 404
