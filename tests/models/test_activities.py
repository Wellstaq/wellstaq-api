"""
UserPriority, DailyCheckin, and UserActivityFeed model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Uses the shared make_user / make_org_with_owner fixtures from conftest
(not local re-implementations) since User.organization_id is NOT NULL
and Organization.owner_id has a circular FK to User — conftest already
resolves that the same way the real signup transaction does.
"""

import pytest
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DailyCheckin,
    Organization,
    User,
    UserActivityFeed,
    UserPriority,
)

from tests.conftest import make_user, make_org_with_owner


# ── Local factories (engagement-model-specific, not in conftest) ───────────

def make_priority(user_id: str, **overrides) -> UserPriority:
    defaults = dict(
        user_id=user_id,
        priority="reduce_stress",
        rank=1,
    )
    return UserPriority(**{**defaults, **overrides})


def make_checkin(user_id: str, **overrides) -> DailyCheckin:
    now = datetime.now(timezone.utc)
    defaults = dict(
        user_id=user_id,
        mood="good",
        energy_level="good",
        stress_level="bad",
        checked_in_at=now,
        checkin_date=now.date(),
    )
    return DailyCheckin(**{**defaults, **overrides})


def make_activity(user_id: str, organization_id: str, **overrides) -> UserActivityFeed:
    defaults = dict(
        user_id=user_id,
        organization_id=organization_id,
        activity_type="joined_club",
        reference_id="00000000-0000-0000-0000-000000000001",
        reference_type="club",
        activity_metadata={"club_name": "Running Club"},
    )
    return UserActivityFeed(**{**defaults, **overrides})


# ── UserPriority model ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_priority_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    priority = make_priority(owner.id)
    db_session.add(priority)
    await db_session.flush()

    result = await db_session.execute(select(UserPriority).where(UserPriority.id == priority.id))
    fetched = result.scalar_one()
    assert fetched.priority == "reduce_stress"
    assert fetched.rank == 1


@pytest.mark.asyncio
async def test_user_can_have_multiple_ranked_priorities(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_priority(owner.id, priority="reduce_stress", rank=1))
    db_session.add(make_priority(owner.id, priority="build_energy", rank=2))
    db_session.add(make_priority(owner.id, priority="improve_balance", rank=3))
    await db_session.flush()

    result = await db_session.execute(
        select(UserPriority).where(UserPriority.user_id == owner.id).order_by(UserPriority.rank)
    )
    priorities = result.scalars().all()
    assert [p.priority for p in priorities] == ["reduce_stress", "build_energy", "improve_balance"]


@pytest.mark.asyncio
async def test_priority_user_fk_must_exist(db_session: AsyncSession):
    priority = make_priority(user_id="00000000-0000-0000-0000-000000000000")
    db_session.add(priority)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_priority_rank_must_be_positive(db_session: AsyncSession):
    """CheckConstraint("rank > 0") — rejects zero and negative ranks."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_priority(owner.id, rank=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_priority_negative_rank_rejected(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_priority(owner.id, rank=-100))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_delete_cascades_to_priorities(db_session: AsyncSession):
    """Deletes a non-owner member, not the owner. Organization.owner_id
    is ondelete="CASCADE", so deleting the *owner* directly cascades to
    the whole org (see Organization model docstring) — that would
    delete this priority row as a side effect of the org/owner cascade,
    not because of UserPriority.user_id's own CASCADE, which is what
    this test is actually meant to isolate and verify."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member = make_user(org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    priority = make_priority(member.id)
    db_session.add(priority)
    await db_session.flush()
    priority_id = priority.id

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(select(UserPriority).where(UserPriority.id == priority_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_priority_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    priority = make_priority(owner.id)
    db_session.add(priority)
    await db_session.flush()
    await db_session.refresh(priority, ["user"])

    assert priority.user is not None
    assert priority.user.id == owner.id


@pytest.mark.asyncio
async def test_user_priorities_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_priority(owner.id, priority="reduce_stress", rank=1))
    db_session.add(make_priority(owner.id, priority="build_energy", rank=2))
    await db_session.flush()
    await db_session.refresh(owner, ["priorities"])

    assert len(owner.priorities) == 2


# ── DailyCheckin model ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkin_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    checkin = make_checkin(owner.id)
    db_session.add(checkin)
    await db_session.flush()

    result = await db_session.execute(select(DailyCheckin).where(DailyCheckin.id == checkin.id))
    fetched = result.scalar_one()
    assert fetched.mood == "good"
    assert fetched.energy_level == "good"
    assert fetched.stress_level == "bad"
    assert fetched.checkin_date == checkin.checkin_date


@pytest.mark.asyncio
async def test_user_cannot_checkin_twice_same_day(db_session: AsyncSession):
    """Unique constraint on (user_id, checkin_date)."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    today = date(2026, 6, 20)
    db_session.add(make_checkin(owner.id, checkin_date=today))
    await db_session.flush()

    db_session.add(make_checkin(owner.id, checkin_date=today, mood="tired"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_can_checkin_on_different_days(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_checkin(owner.id, checkin_date=date(2026, 6, 19)))
    db_session.add(make_checkin(owner.id, checkin_date=date(2026, 6, 20)))
    await db_session.flush()

    result = await db_session.execute(
        select(DailyCheckin).where(DailyCheckin.user_id == owner.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_checkin_user_fk_must_exist(db_session: AsyncSession):
    checkin = make_checkin(user_id="00000000-0000-0000-0000-000000000000")
    db_session.add(checkin)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["mood", "energy_level", "stress_level"])
async def test_checkin_vocabulary_rejects_invalid_value(db_session: AsyncSession, field: str):
    """CheckConstraint restricts mood/energy_level/stress_level to the
    fixed good|bad|stressed|tired vocabulary — dirty variants like
    'Good' or 'GOOD' must be rejected at the DB layer."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_checkin(owner.id, **{field: "Good"}))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["good", "bad", "stressed", "tired"])
async def test_checkin_vocabulary_accepts_valid_values(db_session: AsyncSession, value: str):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(
        make_checkin(owner.id, mood=value, energy_level=value, stress_level=value)
    )
    await db_session.flush()  # should not raise


@pytest.mark.asyncio
async def test_user_delete_cascades_to_checkins(db_session: AsyncSession):
    """Deletes a non-owner member — see test_user_delete_cascades_to_priorities
    for why deleting the owner directly would not isolate this cascade."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member = make_user(org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    checkin = make_checkin(member.id)
    db_session.add(checkin)
    await db_session.flush()
    checkin_id = checkin.id

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(select(DailyCheckin).where(DailyCheckin.id == checkin_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_checkin_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    checkin = make_checkin(owner.id)
    db_session.add(checkin)
    await db_session.flush()
    await db_session.refresh(checkin, ["user"])

    assert checkin.user is not None
    assert checkin.user.id == owner.id


@pytest.mark.asyncio
async def test_user_daily_checkins_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_checkin(owner.id, checkin_date=date(2026, 6, 19)))
    db_session.add(make_checkin(owner.id, checkin_date=date(2026, 6, 20)))
    await db_session.flush()
    await db_session.refresh(owner, ["daily_checkins"])

    assert len(owner.daily_checkins) == 2


# ── UserActivityFeed model ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activity_feed_entry_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(owner.id, org.id)
    db_session.add(activity)
    await db_session.flush()

    result = await db_session.execute(
        select(UserActivityFeed).where(UserActivityFeed.id == activity.id)
    )
    fetched = result.scalar_one()
    assert fetched.activity_type == "joined_club"
    assert fetched.reference_type == "club"
    assert fetched.activity_metadata == {"club_name": "Running Club"}


@pytest.mark.asyncio
async def test_activity_metadata_column_name_is_metadata(db_session: AsyncSession):
    """Python attribute is activity_metadata; underlying DB column is
    literally named 'metadata' (avoids colliding with Base.metadata)."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(owner.id, org.id)
    db_session.add(activity)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT metadata FROM user_activity_feed WHERE id = :id"),
        {"id": activity.id},
    )
    row = result.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_activity_feed_organization_required(db_session: AsyncSession):
    """organization_id is NOT NULL per decision — every activity has an org."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = UserActivityFeed(
        user_id=owner.id,
        organization_id=None,
        activity_type="joined_club",
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_feed_user_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(
        user_id="00000000-0000-0000-0000-000000000000",
        organization_id=org.id,
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_feed_org_fk_must_exist(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(
        user_id=owner.id,
        organization_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference_type, reference_id",
    [
        ("club", None),
        (None, "00000000-0000-0000-0000-000000000001"),
    ],
)
async def test_activity_feed_reference_pairing_rejects_mismatch(
    db_session: AsyncSession, reference_type: str | None, reference_id: str | None
):
    """CheckConstraint — reference_type and reference_id must be both
    NULL or both set, never one without the other."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(
        owner.id,
        org.id,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_feed_reference_pairing_allows_both_null(db_session: AsyncSession):
    """A generic activity with no polymorphic reference at all is valid —
    e.g. a non-referential audit-style entry."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(
        owner.id, org.id, reference_type=None, reference_id=None
    )
    db_session.add(activity)
    await db_session.flush()  # should not raise


@pytest.mark.asyncio
async def test_user_delete_cascades_to_activity_feed(db_session: AsyncSession):
    """Deletes a non-owner member — see test_user_delete_cascades_to_priorities
    for why deleting the owner directly would not isolate this cascade
    (UserActivityFeed.user_id's own CASCADE) from the separate
    owner_id/org cascade."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    member = make_user(org.id, email="member@example.com", branch_id=owner.branch_id)
    db_session.add(member)
    await db_session.flush()

    activity = make_activity(member.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    activity_id = activity.id

    await db_session.delete(member)
    await db_session.flush()

    result = await db_session.execute(
        select(UserActivityFeed).where(UserActivityFeed.id == activity_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_activity_feed(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(owner.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    activity_id = activity.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(UserActivityFeed).where(UserActivityFeed.id == activity_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_activity_feed_user_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(owner.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    await db_session.refresh(activity, ["user"])

    assert activity.user is not None
    assert activity.user.id == owner.id


@pytest.mark.asyncio
async def test_activity_feed_organization_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    activity = make_activity(owner.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    await db_session.refresh(activity, ["organization"])

    assert activity.organization is not None
    assert activity.organization.id == org.id


@pytest.mark.asyncio
async def test_user_activity_feed_relationship(db_session: AsyncSession):
    """user.activity_feed loads all activity rows for that user."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    db_session.add(make_activity(owner.id, org.id, activity_type="joined_club"))
    db_session.add(make_activity(owner.id, org.id, activity_type="created_post"))
    await db_session.flush()
    await db_session.refresh(owner, ["activity_feed"])

    assert len(owner.activity_feed) == 2


@pytest.mark.asyncio
async def test_organization_activity_feed_relationship(db_session: AsyncSession):
    """org.activity_feed loads all activity rows scoped to that org."""
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    user2 = make_user(org.id, email="other@example.com", branch_id=owner.branch_id)
    db_session.add(user2)
    await db_session.flush()

    db_session.add(make_activity(owner.id, org.id))
    db_session.add(make_activity(user2.id, org.id))
    await db_session.flush()
    await db_session.refresh(org, ["activity_feed"])

    assert len(org.activity_feed) == 2


@pytest.mark.asyncio
async def test_activity_feed_user_created_at_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('user_activity_feed')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_user_activity_feed_user_created_at" in index_names
    assert "ix_user_activity_feed_org_created_at" in index_names