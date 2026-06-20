"""
UserPriority, DailyCheckin, and UserActivityFeed model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
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


# ── Factories ──────────────────────────────────────────────────────────────────

def make_user(email: str = "user@example.com", **overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        role_name="employee",
        hashed_password="$2b$12$fakehash",
    )
    return User(**{**defaults, **overrides})


def make_org(owner_id: str, **overrides) -> Organization:
    defaults = dict(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id=owner_id,
    )
    return Organization(**{**defaults, **overrides})


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


async def setup_user_org(db_session: AsyncSession) -> tuple[User, Organization]:
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id)
    db_session.add(org)
    await db_session.flush()

    return user, org


# ── UserPriority model ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_priority_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    priority = make_priority(user.id)
    db_session.add(priority)
    await db_session.flush()

    result = await db_session.execute(select(UserPriority).where(UserPriority.id == priority.id))
    fetched = result.scalar_one()
    assert fetched.priority == "reduce_stress"
    assert fetched.rank == 1


@pytest.mark.asyncio
async def test_user_can_have_multiple_ranked_priorities(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_priority(user.id, priority="reduce_stress", rank=1))
    db_session.add(make_priority(user.id, priority="build_energy", rank=2))
    db_session.add(make_priority(user.id, priority="improve_balance", rank=3))
    await db_session.flush()

    result = await db_session.execute(
        select(UserPriority).where(UserPriority.user_id == user.id).order_by(UserPriority.rank)
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
async def test_user_delete_cascades_to_priorities(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    priority = make_priority(user.id)
    db_session.add(priority)
    await db_session.flush()
    priority_id = priority.id

    await db_session.delete(user)
    await db_session.flush()

    result = await db_session.execute(select(UserPriority).where(UserPriority.id == priority_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_priority_user_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    priority = make_priority(user.id)
    db_session.add(priority)
    await db_session.flush()
    await db_session.refresh(priority, ["user"])

    assert priority.user is not None
    assert priority.user.id == user.id


@pytest.mark.asyncio
async def test_user_priorities_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_priority(user.id, priority="reduce_stress", rank=1))
    db_session.add(make_priority(user.id, priority="build_energy", rank=2))
    await db_session.flush()
    await db_session.refresh(user, ["priorities"])

    assert len(user.priorities) == 2


# ── DailyCheckin model ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkin_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    checkin = make_checkin(user.id)
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
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    today = date(2026, 6, 20)
    db_session.add(make_checkin(user.id, checkin_date=today))
    await db_session.flush()

    db_session.add(make_checkin(user.id, checkin_date=today, mood="tired"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_can_checkin_on_different_days(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_checkin(user.id, checkin_date=date(2026, 6, 19)))
    db_session.add(make_checkin(user.id, checkin_date=date(2026, 6, 20)))
    await db_session.flush()

    result = await db_session.execute(
        select(DailyCheckin).where(DailyCheckin.user_id == user.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_checkin_user_fk_must_exist(db_session: AsyncSession):
    checkin = make_checkin(user_id="00000000-0000-0000-0000-000000000000")
    db_session.add(checkin)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_delete_cascades_to_checkins(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    checkin = make_checkin(user.id)
    db_session.add(checkin)
    await db_session.flush()
    checkin_id = checkin.id

    await db_session.delete(user)
    await db_session.flush()

    result = await db_session.execute(select(DailyCheckin).where(DailyCheckin.id == checkin_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_checkin_user_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    checkin = make_checkin(user.id)
    db_session.add(checkin)
    await db_session.flush()
    await db_session.refresh(checkin, ["user"])

    assert checkin.user is not None
    assert checkin.user.id == user.id


@pytest.mark.asyncio
async def test_user_daily_checkins_relationship(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_checkin(user.id, checkin_date=date(2026, 6, 19)))
    db_session.add(make_checkin(user.id, checkin_date=date(2026, 6, 20)))
    await db_session.flush()
    await db_session.refresh(user, ["daily_checkins"])

    assert len(user.daily_checkins) == 2


# ── UserActivityFeed model ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activity_feed_entry_persists(db_session: AsyncSession):
    user, org = await setup_user_org(db_session)

    activity = make_activity(user.id, org.id)
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
    user, org = await setup_user_org(db_session)

    activity = make_activity(user.id, org.id)
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
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    activity = UserActivityFeed(
        user_id=user.id,
        organization_id=None,
        activity_type="joined_club",
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_feed_user_fk_must_exist(db_session: AsyncSession):
    user, org = await setup_user_org(db_session)

    activity = make_activity(
        user_id="00000000-0000-0000-0000-000000000000",
        organization_id=org.id,
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_activity_feed_org_fk_must_exist(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    activity = make_activity(
        user_id=user.id,
        organization_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(activity)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_delete_cascades_to_activity_feed(db_session: AsyncSession):
    user, org = await setup_user_org(db_session)

    activity = make_activity(user.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    activity_id = activity.id

    # Transfer org ownership before deleting the original user (owner)
    new_owner = make_user(email="new_owner@example.com")
    db_session.add(new_owner)
    await db_session.flush()
    org.owner_id = new_owner.id
    await db_session.flush()

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    await db_session.flush()

    result = await db_session.execute(
        select(UserActivityFeed).where(UserActivityFeed.id == activity_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_activity_feed(db_session: AsyncSession):
    user, org = await setup_user_org(db_session)

    activity = make_activity(user.id, org.id)
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
    user, org = await setup_user_org(db_session)

    activity = make_activity(user.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    await db_session.refresh(activity, ["user"])

    assert activity.user is not None
    assert activity.user.id == user.id


@pytest.mark.asyncio
async def test_activity_feed_organization_relationship(db_session: AsyncSession):
    user, org = await setup_user_org(db_session)

    activity = make_activity(user.id, org.id)
    db_session.add(activity)
    await db_session.flush()
    await db_session.refresh(activity, ["organization"])

    assert activity.organization is not None
    assert activity.organization.id == org.id


@pytest.mark.asyncio
async def test_user_activity_feed_relationship(db_session: AsyncSession):
    """user.activity_feed loads all activity rows for that user."""
    user, org = await setup_user_org(db_session)

    db_session.add(make_activity(user.id, org.id, activity_type="joined_club"))
    db_session.add(make_activity(user.id, org.id, activity_type="created_post"))
    await db_session.flush()
    await db_session.refresh(user, ["activity_feed"])

    assert len(user.activity_feed) == 2


@pytest.mark.asyncio
async def test_organization_activity_feed_relationship(db_session: AsyncSession):
    """org.activity_feed loads all activity rows scoped to that org."""
    user, org = await setup_user_org(db_session)

    user2 = make_user(email="other@example.com")
    db_session.add(user2)
    await db_session.flush()

    db_session.add(make_activity(user.id, org.id))
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