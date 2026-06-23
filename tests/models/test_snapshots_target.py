"""
LeaderboardSnapshot, Target, and KPISnapshot model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

What's tested:
  - LeaderboardSnapshot: persistence, UUID format, computed_at
    auto-set, previous_rank nullable on first run, rank/period
    check constraints, org + user FK enforcement, cascade on org
    and user delete, cross-tenant isolation, composite index exists,
    org and user relationship loading.
  - Target: persistence, UUID format, unique constraint on
    (org, metric_type, period), duplicate insert raises, org FK
    enforcement, cascade on org delete, check constraints (empty
    metric_type, empty period, non-positive target_value), org
    relationship loading.
  - KPISnapshot: persistence, UUID format, unique constraint on
    (org, metric_name, period_start), duplicate insert raises, org
    FK enforcement, cascade on org delete, check constraints (empty
    metric_name, invalid period range), org relationship loading.
"""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeaderboardSnapshot, Target, KPISnapshot
from tests.conftest import make_org_with_owner, make_user


# ── Factories ──────────────────────────────────────────────────────────────────

def make_snapshot(organization_id: str, user_id: str, **overrides) -> LeaderboardSnapshot:
    defaults = dict(
        organization_id=organization_id,
        user_id=user_id,
        metric_type="steps",
        value=Decimal("95000"),
        rank=1,
        previous_rank=None,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 7),
    )
    return LeaderboardSnapshot(**{**defaults, **overrides})


def make_target(organization_id: str, **overrides) -> Target:
    defaults = dict(
        organization_id=organization_id,
        metric_type="steps",
        period="2026-W23",
        target_value=Decimal("70000"),
    )
    return Target(**{**defaults, **overrides})


def make_kpi_snapshot(organization_id: str, **overrides) -> KPISnapshot:
    defaults = dict(
        organization_id=organization_id,
        metric_name="avg_daily_steps",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        value=Decimal("8250.50"),
    )
    return KPISnapshot(**{**defaults, **overrides})


# ── LeaderboardSnapshot ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_leaderboard_snapshot_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.user_id == owner.id
    assert fetched.metric_type == "steps"
    assert fetched.value == Decimal("95000")
    assert fetched.rank == 1
    assert fetched.previous_rank is None
    assert fetched.period_start == date(2026, 6, 1)
    assert fetched.period_end == date(2026, 6, 7)


@pytest.mark.asyncio
async def test_leaderboard_snapshot_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()

    parts = snapshot.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_leaderboard_snapshot_computed_at_auto_set(db_session: AsyncSession):
    """computed_at is set by the DB server default — never manually."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.computed_at is not None


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_rank_nullable(db_session: AsyncSession):
    """previous_rank is null on the very first computation run."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id, previous_rank=None)
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.previous_rank is None


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_rank_set(db_session: AsyncSession):
    """previous_rank carries the prior rank once at least one prior
    snapshot exists."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id, rank=2, previous_rank=5)
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.previous_rank == 5


@pytest.mark.asyncio
async def test_leaderboard_snapshot_multiple_metrics_same_period(db_session: AsyncSession):
    """No uniqueness on (user, metric, period) — the scheduler can
    write steps, distance, and squat rankings independently."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.id, metric_type="steps", rank=1))
    db_session.add(make_snapshot(org.id, owner.id, metric_type="distance", rank=3))
    db_session.add(make_snapshot(org.id, owner.id, metric_type="squat", rank=2))
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.organization_id == org.id,
            LeaderboardSnapshot.user_id == owner.id,
        )
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_leaderboard_snapshot_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        organization_id="00000000-0000-0000-0000-000000000000",
        user_id=owner.id,
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_user_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        organization_id=org.id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_rank_must_be_positive(db_session: AsyncSession):
    """rank > 0 — rank=0 and rank=-1 are both invalid."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.id, rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    # Rollback the dead transaction before the second assertion
    await db_session.rollback()

    db_session.add(make_snapshot(org.id, owner.id, rank=-1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_rank_must_be_positive_when_set(
    db_session: AsyncSession,
):
    """previous_rank must be > 0 when provided — 0 is not a valid rank."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.id, previous_rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_period_range_must_be_valid(db_session: AsyncSession):
    """period_end must not be before period_start."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        org.id, owner.id,
        period_start=date(2026, 6, 30),
        period_end=date(2026, 6, 1),
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_cross_tenant_isolation(db_session: AsyncSession):
    """Org B's query returns no rows when only org A has snapshots."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_snapshot(org_a.id, owner_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.organization_id == org_b.id
        )
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_leaderboard_snapshot_filterable_by_org(db_session: AsyncSession):
    """Snapshots are directly filterable by organization_id."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_snapshot(org_a.id, owner_a.id, rank=1))
    db_session.add(make_snapshot(org_b.id, owner_b.id, rank=1))
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.organization_id == org_a.id
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].organization_id == org_a.id


@pytest.mark.asyncio
async def test_org_delete_cascades_to_leaderboard_snapshots(db_session: AsyncSession):
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    member = make_user(org_a.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    snapshot = make_snapshot(org_a.id, member.id)
    db_session.add(snapshot)
    await db_session.flush()
    snapshot_id = snapshot.id

    await db_session.delete(org_a)
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.id == snapshot_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_delete_cascades_to_leaderboard_snapshots(db_session: AsyncSession):
    """Deletes a non-owner member to isolate the user_id CASCADE from
    the org owner_id constraint."""
    org, owner = await make_org_with_owner(db_session)

    member = make_user(org.id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    snapshot = make_snapshot(org.id, member.id)
    db_session.add(snapshot)
    await db_session.flush()
    snapshot_id = snapshot.id

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.id == snapshot_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_leaderboard_snapshot_composite_index_exists(db_session: AsyncSession):
    result = await db_session.execute(
        text("PRAGMA index_list('leaderboard_snapshots')")
    )
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_leaderboard_snapshots_org_metric_period_rank" in index_names


@pytest.mark.asyncio
async def test_leaderboard_snapshot_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["organization"])

    assert snapshot.organization is not None
    assert snapshot.organization.id == org.id


@pytest.mark.asyncio
async def test_leaderboard_snapshot_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["user"])

    assert snapshot.user is not None
    assert snapshot.user.id == owner.id


# ── Target ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_target_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    target = make_target(org.id)
    db_session.add(target)
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.id == target.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.metric_type == "steps"
    assert fetched.period == "2026-W23"
    assert fetched.target_value == Decimal("70000")


@pytest.mark.asyncio
async def test_target_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    target = make_target(org.id)
    db_session.add(target)
    await db_session.flush()

    parts = target.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_target_duplicate_org_metric_period_raises(db_session: AsyncSession):
    """Unique constraint on (organization_id, metric_type, period) — a
    second insert for the same combination must be rejected."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id))
    await db_session.flush()

    db_session.add(make_target(org.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_same_metric_different_period_allowed(db_session: AsyncSession):
    """Same metric_type in a different period is a distinct target row."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, period="2026-W23"))
    db_session.add(make_target(org.id, period="2026-W24"))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_target_same_period_different_metric_allowed(db_session: AsyncSession):
    """Same period with a different metric_type is also a distinct target."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, metric_type="steps"))
    db_session.add(make_target(org.id, metric_type="distance"))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_target_org_fk_must_exist(db_session: AsyncSession):
    target = make_target(organization_id="00000000-0000-0000-0000-000000000000")
    db_session.add(target)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_metric_type_cannot_be_empty(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, metric_type=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    # Rollback the dead transaction before the second assertion
    await db_session.rollback()

    db_session.add(make_target(org.id, metric_type="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_period_cannot_be_empty(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, period=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    # Rollback the dead transaction before the second assertion
    await db_session.rollback()

    db_session.add(make_target(org.id, period="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_value_must_be_positive(db_session: AsyncSession):
    """target_value > 0 — zero and negative values are rejected."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, target_value=Decimal("0")))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    # Rollback the dead transaction before the second assertion
    await db_session.rollback()

    db_session.add(make_target(org.id, target_value=Decimal("-100")))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_delete_cascades_to_targets(db_session: AsyncSession):
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    target = make_target(org_a.id)
    db_session.add(target)
    await db_session.flush()
    target_id = target.id

    await db_session.delete(org_a)
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.id == target_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_target_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's targets when filtering by org_id."""
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_target(org_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_target_composite_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('targets')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_targets_org_metric_period" in index_names


@pytest.mark.asyncio
async def test_target_organization_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    target = make_target(org.id)
    db_session.add(target)
    await db_session.flush()
    await db_session.refresh(target, ["organization"])

    assert target.organization is not None
    assert target.organization.id == org.id


# ── KPISnapshot ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kpi_snapshot_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id)
    db_session.add(kpi)
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.id == kpi.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.metric_name == "avg_daily_steps"
    assert fetched.period_start == date(2026, 6, 1)
    assert fetched.period_end == date(2026, 6, 30)
    assert fetched.value == Decimal("8250.50")


@pytest.mark.asyncio
async def test_kpi_snapshot_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id)
    db_session.add(kpi)
    await db_session.flush()

    parts = kpi.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_kpi_snapshot_duplicate_org_metric_period_raises(db_session: AsyncSession):
    """Unique constraint on (organization_id, metric_name, period_start)
    — writing the same metric twice for the same period must fail."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_kpi_snapshot(org.id))
    await db_session.flush()

    db_session.add(make_kpi_snapshot(org.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_same_metric_different_period_allowed(db_session: AsyncSession):
    """Same metric_name in a different period_start is a distinct snapshot."""
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_kpi_snapshot(
        org.id,
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    ))
    db_session.add(make_kpi_snapshot(
        org.id,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
    ))
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_kpi_snapshot_org_fk_must_exist(db_session: AsyncSession):
    kpi = make_kpi_snapshot(organization_id="00000000-0000-0000-0000-000000000000")
    db_session.add(kpi)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_metric_name_cannot_be_empty(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    db_session.add(make_kpi_snapshot(org.id, metric_name=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    # Rollback the dead transaction before the second assertion
    await db_session.rollback()

    db_session.add(make_kpi_snapshot(org.id, metric_name="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_period_range_must_be_valid(db_session: AsyncSession):
    """period_end must not be before period_start."""
    org, _ = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(
        org.id,
        period_start=date(2026, 6, 30),
        period_end=date(2026, 6, 1),
    )
    db_session.add(kpi)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_delete_cascades_to_kpi_snapshots(db_session: AsyncSession):
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    kpi = make_kpi_snapshot(org_a.id)
    db_session.add(kpi)
    await db_session.flush()
    kpi_id = kpi.id

    await db_session.delete(org_a)
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.id == kpi_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_kpi_snapshot_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's KPI snapshots when filtering by org_id."""
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Beta Corp"},
        user_kwargs={"email": "owner-b@example.com"},
    )

    db_session.add(make_kpi_snapshot(org_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_kpi_snapshot_composite_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('kpi_snapshots')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_kpi_snapshots_org_metric_period_start" in index_names


@pytest.mark.asyncio
async def test_kpi_snapshot_organization_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id)
    db_session.add(kpi)
    await db_session.flush()
    await db_session.refresh(kpi, ["organization"])

    assert kpi.organization is not None
    assert kpi.organization.id == org.id