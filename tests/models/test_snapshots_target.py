"""
LeaderboardSnapshot, Target, and KPISnapshot model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

NOTE ON ASSUMPTIONS: this rewrite assumes two things about conftest.py
that live outside the files given to me, and that should be double
checked against the real conftest:
  1. `make_org_with_owner` already creates a default Branch and
     assigns it to the returned owner (owner.branch_id is populated).
     This appears to be true today since only the plain `make_user()`
     helper triggered the `users.branch_id NOT NULL` failure in the
     given traceback — the owner path did not.
  2. `make_user(organization_id, **overrides)` forwards **overrides
     the same way make_snapshot/make_target/make_kpi_snapshot do
     below, so passing `branch_id=...` works. If it doesn't yet accept
     that, it needs a one-line change:
         def make_user(organization_id, **overrides):
             defaults = dict(organization_id=organization_id, ...)
             return User(**{**defaults, **overrides})
  A local `make_branch` factory is added below since branch-scoped
  tests need to create additional branches beyond the owner's default
  one; adjust its default fields to match the real Branch model.

What's tested:
  - LeaderboardSnapshot: persistence, UUID format, computed_at
    auto-set, previous_rank/previous_org_rank nullable on first run,
    rank/org_rank/period check constraints, org + branch + user FK
    enforcement, cascade on org/branch/user delete, cross-tenant and
    cross-branch isolation, composite indexes exist, org/branch/user
    relationship loading, and that a single row can carry both a
    branch-scoped rank and an org-wide rank consistent with values
    across multiple branches.
  - Target: persistence, UUID format, unique constraint on
    (branch, metric_type, period), duplicate insert raises, org +
    branch FK enforcement, cascade on org/branch delete, check
    constraints (empty metric_type, empty period, non-positive
    target_value), org/branch relationship loading, org-wide rollup
    index exists.
  - KPISnapshot: persistence, UUID format, unique constraint on
    (branch, metric_name, period_start), duplicate insert raises,
    org + branch FK enforcement, cascade on org/branch delete, check
    constraints (empty metric_name, invalid period range), org/branch
    relationship loading, org-wide rollup index exists.
"""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeaderboardSnapshot, Target, KPISnapshot, Branch
from tests.conftest import make_org_with_owner, make_user


# ── Factories ──────────────────────────────────────────────────────────────────

def make_branch(organization_id: str, **overrides) -> Branch:
    """Local factory — adjust default fields to match the real Branch model."""
    defaults = dict(
        organization_id=organization_id,
        name="Downtown Branch",
    )
    return Branch(**{**defaults, **overrides})


def make_snapshot(organization_id: str, branch_id: str, user_id: str, **overrides) -> LeaderboardSnapshot:
    defaults = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        user_id=user_id,
        metric_type="steps",
        value=Decimal("95000"),
        rank=1,
        previous_rank=None,
        org_rank=1,
        previous_org_rank=None,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 7),
    )
    return LeaderboardSnapshot(**{**defaults, **overrides})


def make_target(organization_id: str, branch_id: str, **overrides) -> Target:
    defaults = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        metric_type="steps",
        period="2026-W23",
        target_value=Decimal("70000"),
    )
    return Target(**{**defaults, **overrides})


def make_kpi_snapshot(organization_id: str, branch_id: str, **overrides) -> KPISnapshot:
    defaults = dict(
        organization_id=organization_id,
        branch_id=branch_id,
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

    snapshot = make_snapshot(org.id, owner.branch_id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(LeaderboardSnapshot.id == snapshot.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.branch_id == owner.branch_id
    assert fetched.user_id == owner.id
    assert fetched.metric_type == "steps"
    assert fetched.value == Decimal("95000")
    assert fetched.rank == 1
    assert fetched.previous_rank is None
    assert fetched.org_rank == 1
    assert fetched.previous_org_rank is None
    assert fetched.period_start == date(2026, 6, 1)
    assert fetched.period_end == date(2026, 6, 7)


@pytest.mark.asyncio
async def test_leaderboard_snapshot_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.branch_id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()

    parts = snapshot.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_leaderboard_snapshot_computed_at_auto_set(db_session: AsyncSession):
    """computed_at is set by the DB server default — never manually."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.branch_id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.computed_at is not None


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_rank_nullable(db_session: AsyncSession):
    """previous_rank and previous_org_rank are null on the very first
    computation run."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        org.id, owner.branch_id, owner.id,
        previous_rank=None, previous_org_rank=None,
    )
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.previous_rank is None
    assert snapshot.previous_org_rank is None


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_rank_set(db_session: AsyncSession):
    """previous_rank/previous_org_rank carry the prior ranks once at
    least one prior snapshot exists."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        org.id, owner.branch_id, owner.id,
        rank=2, previous_rank=5,
        org_rank=4, previous_org_rank=9,
    )
    db_session.add(snapshot)
    await db_session.flush()

    assert snapshot.previous_rank == 5
    assert snapshot.previous_org_rank == 9


@pytest.mark.asyncio
async def test_leaderboard_snapshot_multiple_metrics_same_period(db_session: AsyncSession):
    """No uniqueness on (user, metric, period) — the scheduler can
    write steps, distance, and squat rankings independently."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, metric_type="steps", rank=1, org_rank=1))
    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, metric_type="distance", rank=3, org_rank=6))
    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, metric_type="squat", rank=2, org_rank=4))
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
        branch_id=owner.branch_id,
        user_id=owner.id,
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_branch_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        organization_id=org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
        user_id=owner.id,
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()



@pytest.mark.asyncio
async def test_leaderboard_snapshot_branch_id_required(db_session: AsyncSession):
    """branch_id is NOT NULL — every leaderboard row must be scoped
    to a branch, since every user belongs to exactly one."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, None, owner.id)  # Pass None positionally
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()

@pytest.mark.asyncio
async def test_leaderboard_snapshot_user_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        organization_id=org.id,
        branch_id=owner.branch_id,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(snapshot)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_rank_must_be_positive(db_session: AsyncSession):
    """rank > 0 — rank=0 and rank=-1 are both invalid."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, rank=-1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_org_rank_must_be_positive(db_session: AsyncSession):
    """org_rank > 0 — org_rank=0 and org_rank=-1 are both invalid."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, org_rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, org_rank=-1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_rank_must_be_positive_when_set(
    db_session: AsyncSession,
):
    """previous_rank must be > 0 when provided — 0 is not a valid rank."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, previous_rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_previous_org_rank_must_be_positive_when_set(
    db_session: AsyncSession,
):
    """previous_org_rank must be > 0 when provided — 0 is not valid."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, previous_org_rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_leaderboard_snapshot_period_range_must_be_valid(db_session: AsyncSession):
    """period_end must not be before period_start."""
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(
        org.id, owner.branch_id, owner.id,
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

    db_session.add(make_snapshot(org_a.id, owner_a.branch_id, owner_a.id))
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.organization_id == org_b.id
        )
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_leaderboard_snapshot_cross_branch_isolation(db_session: AsyncSession):
    """Filtering by branch_id only returns that branch's rows, even
    within the same org."""
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    member = make_user(org.id, email="member@example.com", branch_id=branch_b.id)
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_snapshot(org.id, owner.branch_id, owner.id, rank=1, org_rank=1))
    db_session.add(make_snapshot(org.id, branch_b.id, member.id, rank=1, org_rank=2))
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.branch_id == branch_b.id
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == member.id


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

    db_session.add(make_snapshot(org_a.id, owner_a.branch_id, owner_a.id, rank=1, org_rank=1))
    db_session.add(make_snapshot(org_b.id, owner_b.branch_id, owner_b.id, rank=1, org_rank=1))
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
async def test_leaderboard_snapshot_branch_rank_and_org_rank_across_branches(
    db_session: AsyncSession,
):
    """Demonstrates the schema supports the org-wide algorithm: two
    branches, each with their own local #1, but a combined org-wide
    ordering derived from raw values. Branch A's top performer can
    still be behind Branch B's top performer at the org level."""
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    member = make_user(org.id, email="member@example.com", branch_id=branch_b.id)
    db_session.add(member)
    await db_session.flush()

    # Owner is #1 in their branch but has a lower raw value than the
    # member, so org-wide they come second.
    db_session.add(make_snapshot(
        org.id, owner.branch_id, owner.id,
        value=Decimal("80000"), rank=1, org_rank=2,
    ))
    db_session.add(make_snapshot(
        org.id, branch_b.id, member.id,
        value=Decimal("95000"), rank=1, org_rank=1,
    ))
    await db_session.flush()

    result = await db_session.execute(
        select(LeaderboardSnapshot)
        .where(LeaderboardSnapshot.organization_id == org.id)
        .order_by(LeaderboardSnapshot.org_rank)
    )
    rows = result.scalars().all()
    assert [r.user_id for r in rows] == [member.id, owner.id]
    assert all(r.rank == 1 for r in rows)  # both are #1 locally


@pytest.mark.asyncio
async def test_org_delete_cascades_to_leaderboard_snapshots(db_session: AsyncSession):
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    member = make_user(org_a.id, email="member@example.com", branch_id=owner_a.branch_id)
    db_session.add(member)
    await db_session.flush()

    snapshot = make_snapshot(org_a.id, owner_a.branch_id, member.id)
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
async def test_branch_delete_cascades_to_leaderboard_snapshots(db_session: AsyncSession):
    """Deletes a second, non-default branch to isolate the branch_id
    CASCADE from the org's own FK constraints."""
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    member = make_user(org.id, email="member@example.com", branch_id=branch_b.id)
    db_session.add(member)
    await db_session.flush()

    snapshot = make_snapshot(org.id, branch_b.id, member.id)
    db_session.add(snapshot)
    await db_session.flush()
    snapshot_id = snapshot.id

    await db_session.delete(branch_b)
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

    member = make_user(org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    snapshot = make_snapshot(org.id, owner.branch_id, member.id)
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
async def test_leaderboard_snapshot_composite_indexes_exist(db_session: AsyncSession):
    result = await db_session.execute(
        text("PRAGMA index_list('leaderboard_snapshots')")
    )
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_leaderboard_snapshots_branch_metric_period_rank" in index_names
    assert "ix_leaderboard_snapshots_org_metric_period_org_rank" in index_names


@pytest.mark.asyncio
async def test_leaderboard_snapshot_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.branch_id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["organization"])

    assert snapshot.organization is not None
    assert snapshot.organization.id == org.id


@pytest.mark.asyncio
async def test_leaderboard_snapshot_branch_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.branch_id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["branch"])

    assert snapshot.branch is not None
    assert snapshot.branch.id == owner.branch_id


@pytest.mark.asyncio
async def test_leaderboard_snapshot_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    snapshot = make_snapshot(org.id, owner.branch_id, owner.id)
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.refresh(snapshot, ["user"])

    assert snapshot.user is not None
    assert snapshot.user.id == owner.id


# ── Target ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_target_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(org.id, owner.branch_id)
    db_session.add(target)
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.id == target.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.branch_id == owner.branch_id
    assert fetched.metric_type == "steps"
    assert fetched.period == "2026-W23"
    assert fetched.target_value == Decimal("70000")


@pytest.mark.asyncio
async def test_target_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(org.id, owner.branch_id)
    db_session.add(target)
    await db_session.flush()

    parts = target.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_target_duplicate_branch_metric_period_raises(db_session: AsyncSession):
    """Unique constraint on (branch_id, metric_type, period) — a
    second insert for the same combination must be rejected."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, owner.branch_id))
    await db_session.flush()

    db_session.add(make_target(org.id, owner.branch_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_same_metric_period_different_branches_allowed(db_session: AsyncSession):
    """Same org, same metric_type, same period, but two DIFFERENT
    branches is allowed — each branch sets its own target."""
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    db_session.add(make_target(org.id, owner.branch_id))
    db_session.add(make_target(org.id, branch_b.id))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_target_same_metric_different_period_allowed(db_session: AsyncSession):
    """Same branch, same metric_type in a different period is a
    distinct target row."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, owner.branch_id, period="2026-W23"))
    db_session.add(make_target(org.id, owner.branch_id, period="2026-W24"))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.branch_id == owner.branch_id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_target_same_period_different_metric_allowed(db_session: AsyncSession):
    """Same branch, same period with a different metric_type is also
    a distinct target."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, owner.branch_id, metric_type="steps"))
    db_session.add(make_target(org.id, owner.branch_id, metric_type="distance"))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.branch_id == owner.branch_id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_target_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(
        organization_id="00000000-0000-0000-0000-000000000000",
        branch_id=owner.branch_id,
    )
    db_session.add(target)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_branch_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(
        organization_id=org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(target)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_branch_id_required(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(org.id, None)  # Pass None positionally
    db_session.add(target)
    with pytest.raises(IntegrityError):
        await db_session.flush()

@pytest.mark.asyncio
async def test_target_metric_type_cannot_be_empty(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, owner.branch_id, metric_type=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_target(org.id, owner.branch_id, metric_type="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_period_cannot_be_empty(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, owner.branch_id, period=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_target(org.id, owner.branch_id, period="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_target_value_must_be_positive(db_session: AsyncSession):
    """target_value > 0 — zero and negative values are rejected."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_target(org.id, owner.branch_id, target_value=Decimal("0")))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_target(org.id, owner.branch_id, target_value=Decimal("-100")))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_delete_cascades_to_targets(db_session: AsyncSession):
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    target = make_target(org_a.id, owner_a.branch_id)
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
async def test_branch_delete_cascades_to_targets(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    target = make_target(org.id, branch_b.id)
    db_session.add(target)
    await db_session.flush()
    target_id = target.id

    await db_session.delete(branch_b)
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.id == target_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_target_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's targets when filtering by org_id."""
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

    db_session.add(make_target(org_a.id, owner_a.branch_id))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(Target.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_target_branch_and_org_rollup_indexes_exist(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('targets')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_targets_branch_metric_period" in index_names
    assert "ix_targets_org_metric_period" in index_names


@pytest.mark.asyncio
async def test_target_org_wide_rollup_sums_branch_targets(db_session: AsyncSession):
    """Sanity check for the org-wide rollup story: an org-wide target
    figure is SUM(target_value) across branches for the same
    metric/period — no separate stored row needed."""
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    db_session.add(make_target(org.id, owner.branch_id, target_value=Decimal("70000")))
    db_session.add(make_target(org.id, branch_b.id, target_value=Decimal("50000")))
    await db_session.flush()

    result = await db_session.execute(
        select(Target).where(
            Target.organization_id == org.id,
            Target.metric_type == "steps",
            Target.period == "2026-W23",
        )
    )
    rows = result.scalars().all()
    org_wide_total = sum(r.target_value for r in rows)
    assert org_wide_total == Decimal("120000")


@pytest.mark.asyncio
async def test_target_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(org.id, owner.branch_id)
    db_session.add(target)
    await db_session.flush()
    await db_session.refresh(target, ["organization"])

    assert target.organization is not None
    assert target.organization.id == org.id


@pytest.mark.asyncio
async def test_target_branch_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    target = make_target(org.id, owner.branch_id)
    db_session.add(target)
    await db_session.flush()
    await db_session.refresh(target, ["branch"])

    assert target.branch is not None
    assert target.branch.id == owner.branch_id


# ── KPISnapshot ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kpi_snapshot_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id, owner.branch_id)
    db_session.add(kpi)
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.id == kpi.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.branch_id == owner.branch_id
    assert fetched.metric_name == "avg_daily_steps"
    assert fetched.period_start == date(2026, 6, 1)
    assert fetched.period_end == date(2026, 6, 30)
    assert fetched.value == Decimal("8250.50")


@pytest.mark.asyncio
async def test_kpi_snapshot_id_is_uuid_format(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id, owner.branch_id)
    db_session.add(kpi)
    await db_session.flush()

    parts = kpi.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_kpi_snapshot_duplicate_branch_metric_period_raises(db_session: AsyncSession):
    """Unique constraint on (branch_id, metric_name, period_start) —
    writing the same metric twice for the same branch/period must fail."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_kpi_snapshot(org.id, owner.branch_id))
    await db_session.flush()

    db_session.add(make_kpi_snapshot(org.id, owner.branch_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_same_metric_period_different_branches_allowed(db_session: AsyncSession):
    """Same org, same metric, same period_start, but different
    branches is allowed — each branch has its own KPI figure."""
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    db_session.add(make_kpi_snapshot(org.id, owner.branch_id))
    db_session.add(make_kpi_snapshot(org.id, branch_b.id))
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_kpi_snapshot_same_metric_different_period_allowed(db_session: AsyncSession):
    """Same branch, same metric_name in a different period_start is a
    distinct snapshot."""
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_kpi_snapshot(
        org.id, owner.branch_id,
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    ))
    db_session.add(make_kpi_snapshot(
        org.id, owner.branch_id,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
    ))
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.branch_id == owner.branch_id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_kpi_snapshot_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(
        organization_id="00000000-0000-0000-0000-000000000000",
        branch_id=owner.branch_id,
    )
    db_session.add(kpi)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_branch_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(
        organization_id=org.id,
        branch_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(kpi)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_branch_id_required(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id, None)  # Pass None positionally
    db_session.add(kpi)
    with pytest.raises(IntegrityError):
        await db_session.flush()

@pytest.mark.asyncio
async def test_kpi_snapshot_metric_name_cannot_be_empty(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    db_session.add(make_kpi_snapshot(org.id, owner.branch_id, metric_name=""))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()

    db_session.add(make_kpi_snapshot(org.id, owner.branch_id, metric_name="   "))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_kpi_snapshot_period_range_must_be_valid(db_session: AsyncSession):
    """period_end must not be before period_start."""
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(
        org.id, owner.branch_id,
        period_start=date(2026, 6, 30),
        period_end=date(2026, 6, 1),
    )
    db_session.add(kpi)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_org_delete_cascades_to_kpi_snapshots(db_session: AsyncSession):
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a"},
        user_kwargs={"email": "owner-a@example.com"},
    )

    kpi = make_kpi_snapshot(org_a.id, owner_a.branch_id)
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
async def test_branch_delete_cascades_to_kpi_snapshots(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    branch_b = make_branch(org.id, name="Uptown Branch")
    db_session.add(branch_b)
    await db_session.flush()

    kpi = make_kpi_snapshot(org.id, branch_b.id)
    db_session.add(kpi)
    await db_session.flush()
    kpi_id = kpi.id

    await db_session.delete(branch_b)
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.id == kpi_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_kpi_snapshot_cross_tenant_isolation(db_session: AsyncSession):
    """Org B cannot see org A's KPI snapshots when filtering by org_id."""
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

    db_session.add(make_kpi_snapshot(org_a.id, owner_a.branch_id))
    await db_session.flush()

    result = await db_session.execute(
        select(KPISnapshot).where(KPISnapshot.organization_id == org_b.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_kpi_snapshot_branch_and_org_rollup_indexes_exist(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('kpi_snapshots')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_kpi_snapshots_branch_metric_period_start" in index_names
    assert "ix_kpi_snapshots_org_metric_period_start" in index_names


@pytest.mark.asyncio
async def test_kpi_snapshot_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id, owner.branch_id)
    db_session.add(kpi)
    await db_session.flush()
    await db_session.refresh(kpi, ["organization"])

    assert kpi.organization is not None
    assert kpi.organization.id == org.id


@pytest.mark.asyncio
async def test_kpi_snapshot_branch_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    kpi = make_kpi_snapshot(org.id, owner.branch_id)
    db_session.add(kpi)
    await db_session.flush()
    await db_session.refresh(kpi, ["branch"])

    assert kpi.branch is not None
    assert kpi.branch.id == owner.branch_id