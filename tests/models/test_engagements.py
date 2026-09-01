"""
ActivityLog and EngagementLog model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

organization_id and branch_id are present on both tables for tenant
and branch isolation. Tests cover org/branch scoping, cross-tenant
isolation, and cascade deletes through organization_id, branch_id,
and user_id.

What's tested:
  - ActivityLog: persistence, metric_type/unit free fields, value as
    Numeric (decimal precision preserved), nullable unit (steps has
    none), no DB-level daily-uniqueness (multiple rows per user per
    day are allowed), cascade on org/branch/user delete, composite
    indexes exist, date-range query pattern, org filtering and
    cross-tenant isolation.
  - EngagementLog: same shape/behaviour as ActivityLog, backend-written
    score semantics.
"""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, Branch, EngagementLog, Organization, User
from tests.conftest import make_user, make_org_with_owner


# ── Factories ──────────────────────────────────────────────────────────────────

def make_activity_log(
    organization_id: str, branch_id: str, user_id: str, **overrides
) -> ActivityLog:
    defaults = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        user_id=user_id,
        metric_type="steps",
        value=Decimal("8500"),
        unit=None,
        date=date(2026, 6, 20),
    )
    return ActivityLog(**{**defaults, **overrides})


def make_engagement_log(
    organization_id: str, branch_id: str, user_id: str, **overrides
) -> EngagementLog:
    defaults = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        user_id=user_id,
        metric_type="participation",
        date=date(2026, 6, 20),
        value=Decimal("72.5"),
    )
    return EngagementLog(**{**defaults, **overrides})


# ── ActivityLog ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activity_log_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()

    result = await db_session.execute(select(ActivityLog).where(ActivityLog.id == log.id))
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.branch_id == owner.branch_id
    assert fetched.user_id == owner.id
    assert fetched.metric_type == "steps"
    assert fetched.value == Decimal("8500")
    assert fetched.unit is None
    assert fetched.date == date(2026, 6, 20)


@pytest.mark.asyncio
async def test_activity_log_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()

    parts = log.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_activity_log_unit_set_for_distance_metric(db_session: AsyncSession):
    """distance metric_type carries a unit (km), unlike steps."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(
        org.id, owner.branch_id, owner.id,
        metric_type="distance", value=Decimal("5.42"), unit="km",
    )
    db_session.add(log)
    await db_session.flush()

    result = await db_session.execute(select(ActivityLog).where(ActivityLog.id == log.id))
    fetched = result.scalar_one()
    assert fetched.metric_type == "distance"
    assert fetched.unit == "km"
    assert fetched.value == Decimal("5.42")


@pytest.mark.asyncio
async def test_activity_log_value_preserves_decimal_precision(db_session: AsyncSession):
    """value is Numeric — a run distance like 5.42 km must not be
    rounded or coerced through float lossily."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(
        org.id, owner.branch_id, owner.id,
        metric_type="run", value=Decimal("5.42"), unit="km",
    )
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log)

    assert log.value == Decimal("5.42")


@pytest.mark.asyncio
async def test_activity_log_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(
        organization_id="00000000-0000-0000-0000-000000000000",
        branch_id=owner.branch_id,
        user_id=owner.id,
    )
    db_session.add(log)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_log_branch_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(
        organization_id=org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
        user_id=owner.id,
    )
    db_session.add(log)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_log_user_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(
        organization_id=org.id,
        branch_id=owner.branch_id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(log)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_multiple_metric_types_same_day_allowed(db_session: AsyncSession):
    """A user can log several different metrics on the same day."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="steps", value=Decimal("9000")))
    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="squat", value=Decimal("30"), unit="reps"))
    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="run", value=Decimal("3.1"), unit="km"))
    await db_session.flush()

    result = await db_session.execute(
        select(ActivityLog).where(
            ActivityLog.organization_id == org.id,
            ActivityLog.user_id == owner.id,
            ActivityLog.date == date(2026, 6, 20),
        )
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_duplicate_metric_same_user_same_day_not_blocked(db_session: AsyncSession):
    """No DB-level uniqueness on (user_id, metric_type, date) — the
    service layer decides whether a second same-day same-metric entry
    is a correction/append."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="steps", value=Decimal("8500")))
    await db_session.flush()

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="steps", value=Decimal("9200")))
    await db_session.flush()  # should not raise

    result = await db_session.execute(
        select(ActivityLog).where(
            ActivityLog.organization_id == org.id,
            ActivityLog.user_id == owner.id,
            ActivityLog.metric_type == "steps",
            ActivityLog.date == date(2026, 6, 20),
        )
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_activity_log_query_by_date_range(db_session: AsyncSession):
    """The Employee Engagement chart's core query pattern: scan a
    user's logs across a date range."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, date=date(2026, 6, 18)))
    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, date=date(2026, 6, 19)))
    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, date=date(2026, 6, 20)))
    await db_session.flush()

    result = await db_session.execute(
        select(ActivityLog).where(
            ActivityLog.organization_id == org.id,
            ActivityLog.user_id == owner.id,
            ActivityLog.date >= date(2026, 6, 19),
            ActivityLog.date <= date(2026, 6, 20),
        )
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_activity_log_filterable_by_org(db_session: AsyncSession):
    """Logs from org A are directly queryable by organization_id."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_activity_log(org_a.id, owner_a.branch_id, owner_a.id, value=Decimal("1000")))
    db_session.add(make_activity_log(org_b.id, owner_b.branch_id, owner_b.id, value=Decimal("2000")))
    await db_session.flush()

    result = await db_session.execute(
        select(ActivityLog).where(ActivityLog.organization_id == org_a.id)
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].value == Decimal("1000")


@pytest.mark.asyncio
async def test_activity_log_filterable_by_branch(db_session: AsyncSession):
    """Within one org, logs are directly queryable by branch_id — the
    per-branch dashboard scoping pattern. An org-wide rollup instead
    omits the branch_id filter and aggregates across all of the org's
    branches."""
    from tests.conftest import make_branch

    org, owner = await make_org_with_owner(db_session, with_trial=False)

    second_branch = make_branch(org.id, name="Downtown")
    db_session.add(second_branch)
    await db_session.flush()

    branch_member = make_user(
        org.id, email="branch-member@example.com", branch_id=second_branch.id
    )
    db_session.add(branch_member)
    await db_session.flush()

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, value=Decimal("1000")))
    db_session.add(make_activity_log(org.id, second_branch.id, branch_member.id, value=Decimal("2000")))
    await db_session.flush()

    result = await db_session.execute(
        select(ActivityLog).where(
            ActivityLog.organization_id == org.id,
            ActivityLog.branch_id == second_branch.id,
        )
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].value == Decimal("2000")


@pytest.mark.asyncio
async def test_activity_log_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's activity logs when filtering by org_id."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_activity_log(org_a.id, owner_a.branch_id, owner_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(ActivityLog).where(ActivityLog.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_activity_log_user_date_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('activity_logs')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_activity_logs_org_user_date" in index_names


@pytest.mark.asyncio
async def test_activity_log_branch_date_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('activity_logs')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_activity_logs_org_branch_date" in index_names


@pytest.mark.asyncio
async def test_org_delete_cascades_to_activity_logs(db_session: AsyncSession):
    org_a, owner_a = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    member = make_user(org_a.id, email="member@example.com", branch_id=owner_a.branch_id)
    db_session.add(member)
    await db_session.flush()

    log = make_activity_log(org_a.id, member.branch_id, member.id)
    db_session.add(log)
    await db_session.flush()
    log_id = log.id

    await db_session.delete(org_a)
    await db_session.flush()

    result = await db_session.execute(select(ActivityLog).where(ActivityLog.id == log_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_branch_delete_cascades_to_activity_logs(db_session: AsyncSession):
    """Deleting a branch removes its activity logs without requiring
    the org itself to be deleted."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    log_id = log.id

    branch_result = await db_session.execute(
        select(Branch).where(Branch.id == owner.branch_id)
    )
    branch = branch_result.scalar_one()
    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(select(ActivityLog).where(ActivityLog.id == log_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_delete_cascades_to_activity_logs(db_session: AsyncSession):
    """Deletes a non-owner member to isolate ActivityLog.user_id CASCADE
    from the org owner_id constraint."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    member = make_user(org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    log = make_activity_log(org.id, member.branch_id, member.id)
    db_session.add(log)
    await db_session.flush()
    log_id = log.id

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(select(ActivityLog).where(ActivityLog.id == log_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_activity_log_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log, ["organization"])

    assert log.organization is not None
    assert log.organization.id == org.id


@pytest.mark.asyncio
async def test_activity_log_branch_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log, ["branch"])

    assert log.branch is not None
    assert log.branch.id == owner.branch_id


@pytest.mark.asyncio
async def test_activity_log_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_activity_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log, ["user"])

    assert log.user is not None
    assert log.user.id == owner.id


@pytest.mark.asyncio
async def test_organization_activity_logs_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="steps"))
    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="squat", unit="reps"))
    await db_session.flush()
    await db_session.refresh(org, ["activity_logs"])

    assert len(org.activity_logs) == 2


@pytest.mark.asyncio
async def test_user_activity_logs_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="steps"))
    db_session.add(make_activity_log(org.id, owner.branch_id, owner.id, metric_type="squat", unit="reps"))
    await db_session.flush()
    await db_session.refresh(owner, ["activity_logs"])

    assert len(owner.activity_logs) == 2


# ── EngagementLog ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_engagement_log_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()

    result = await db_session.execute(select(EngagementLog).where(EngagementLog.id == log.id))
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.branch_id == owner.branch_id
    assert fetched.user_id == owner.id
    assert fetched.metric_type == "participation"
    assert fetched.value == Decimal("72.5")
    assert fetched.date == date(2026, 6, 20)


@pytest.mark.asyncio
async def test_engagement_log_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()

    parts = log.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_engagement_log_value_preserves_decimal_precision(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="social", value=Decimal("64.25"))
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log)

    assert log.value == Decimal("64.25")


@pytest.mark.asyncio
async def test_engagement_log_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(
        organization_id="00000000-0000-0000-0000-000000000000",
        branch_id=owner.branch_id,
        user_id=owner.id,
    )
    db_session.add(log)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_engagement_log_branch_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(
        organization_id=org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
        user_id=owner.id,
    )
    db_session.add(log)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_engagement_log_user_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(
        organization_id=org.id,
        branch_id=owner.branch_id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(log)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_multiple_engagement_metric_types_same_day_allowed(db_session: AsyncSession):
    """Backend job can write several scores for the same day."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="participation"))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="checkins"))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="social"))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="productivity"))
    await db_session.flush()

    result = await db_session.execute(
        select(EngagementLog).where(
            EngagementLog.organization_id == org.id,
            EngagementLog.user_id == owner.id,
            EngagementLog.date == date(2026, 6, 20),
        )
    )
    assert len(result.scalars().all()) == 4


@pytest.mark.asyncio
async def test_engagement_log_query_by_date_range(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, date=date(2026, 6, 1)))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, date=date(2026, 6, 15)))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, date=date(2026, 6, 30)))
    await db_session.flush()

    result = await db_session.execute(
        select(EngagementLog).where(
            EngagementLog.organization_id == org.id,
            EngagementLog.user_id == owner.id,
            EngagementLog.date >= date(2026, 6, 1),
            EngagementLog.date <= date(2026, 6, 30),
        )
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_engagement_log_filterable_by_org(db_session: AsyncSession):
    """Logs from org A are directly queryable by organization_id."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_engagement_log(org_a.id, owner_a.branch_id, owner_a.id, value=Decimal("80.0")))
    db_session.add(make_engagement_log(org_b.id, owner_b.branch_id, owner_b.id, value=Decimal("60.0")))
    await db_session.flush()

    result = await db_session.execute(
        select(EngagementLog).where(EngagementLog.organization_id == org_a.id)
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].value == Decimal("80.0")


@pytest.mark.asyncio
async def test_engagement_log_filterable_by_branch(db_session: AsyncSession):
    """Within one org, engagement logs are directly queryable by
    branch_id — same per-branch scoping pattern as ActivityLog."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, value=Decimal("50.0")))
    await db_session.flush()

    result = await db_session.execute(
        select(EngagementLog).where(
            EngagementLog.organization_id == org.id,
            EngagementLog.branch_id == owner.branch_id,
        )
    )
    logs = result.scalars().all()
    assert len(logs) == 1


@pytest.mark.asyncio
async def test_engagement_log_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's engagement logs when filtering by org_id."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_engagement_log(org_a.id, owner_a.branch_id, owner_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(EngagementLog).where(EngagementLog.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_engagement_log_user_date_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('engagement_logs')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_engagement_logs_org_user_date" in index_names


@pytest.mark.asyncio
async def test_engagement_log_branch_date_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('engagement_logs')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_engagement_logs_org_branch_date" in index_names


@pytest.mark.asyncio
async def test_org_delete_cascades_to_engagement_logs(db_session: AsyncSession):
    org_a, owner_a = await make_org_with_owner(
        db_session,
        with_trial=False, org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    member = make_user(org_a.id, email="member@example.com", branch_id=owner_a.branch_id)
    db_session.add(member)
    await db_session.flush()

    log = make_engagement_log(org_a.id, member.branch_id, member.id)
    db_session.add(log)
    await db_session.flush()
    log_id = log.id

    await db_session.delete(org_a)
    await db_session.flush()

    result = await db_session.execute(select(EngagementLog).where(EngagementLog.id == log_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_branch_delete_cascades_to_engagement_logs(db_session: AsyncSession):
    """Deleting a branch removes its engagement logs without requiring
    the org itself to be deleted."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    log_id = log.id

    branch_result = await db_session.execute(
        select(Branch).where(Branch.id == owner.branch_id)
    )
    branch = branch_result.scalar_one()
    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(select(EngagementLog).where(EngagementLog.id == log_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_delete_cascades_to_engagement_logs(db_session: AsyncSession):
    """Deletes a non-owner member to isolate EngagementLog.user_id CASCADE
    from the org owner_id constraint."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    member = make_user(org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    log = make_engagement_log(org.id, member.branch_id, member.id)
    db_session.add(log)
    await db_session.flush()
    log_id = log.id

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(select(EngagementLog).where(EngagementLog.id == log_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_engagement_log_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log, ["organization"])

    assert log.organization is not None
    assert log.organization.id == org.id


@pytest.mark.asyncio
async def test_engagement_log_branch_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log, ["branch"])

    assert log.branch is not None
    assert log.branch.id == owner.branch_id


@pytest.mark.asyncio
async def test_engagement_log_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    log = make_engagement_log(org.id, owner.branch_id, owner.id)
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log, ["user"])

    assert log.user is not None
    assert log.user.id == owner.id


@pytest.mark.asyncio
async def test_organization_engagement_logs_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="participation"))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="checkins"))
    await db_session.flush()
    await db_session.refresh(org, ["engagement_logs"])

    assert len(org.engagement_logs) == 2


@pytest.mark.asyncio
async def test_user_engagement_logs_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="participation"))
    db_session.add(make_engagement_log(org.id, owner.branch_id, owner.id, metric_type="checkins"))
    await db_session.flush()
    await db_session.refresh(owner, ["engagement_logs"])

    assert len(owner.engagement_logs) == 2