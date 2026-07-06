"""
Event, EventParticipant tests.

All tests are async, isolated per test via rollback in conftest.
Follows the same helper-based pattern used in the Club/User tests.

Factory note:
make_user() and make_org_with_owner() are imported from conftest.py.
setup_org() is a thin wrapper preserving (user, org) return order.
make_event() is unchanged aside from now always requiring branch_id —
Event.organization_id and Event.branch_id are both NOT NULL / CASCADE:
there is no platform-wide or org-wide (branch-less) event anymore.
All additional users (members, new_owner) are created via
make_user(organization_id=org.id, branch_id=...) since both
User.organization_id and User.branch_id are NOT NULL.
"""

import pytest
from datetime import date, datetime, time as dt_time, timezone
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Event, EventParticipant, Branch, Organization, User
from tests.conftest import make_user, make_org_with_owner, make_branch


# ── Factories ──────────────────────────────────────────────────────────────────

def make_event(**overrides) -> Event:
    defaults = dict(
        title="Annual Meetup",
        start_date=date(2026, 7, 1),
        time=dt_time(18, 0, 0),
        status="scheduled",
    )
    return Event(**{**defaults, **overrides})


def make_participant(event_id: str, user_id: str, **overrides) -> EventParticipant:
    return EventParticipant(event_id=event_id, user_id=user_id, **overrides)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def setup_org(db_session: AsyncSession) -> tuple[User, Organization]:
    """Thin wrapper around the shared make_org_with_owner() factory.
    Preserves this file's original (user, org) return order."""
    org, user = await make_org_with_owner(db_session)
    return user, org


async def setup_event(db_session: AsyncSession) -> tuple[User, Organization, Event]:
    """Every event needs organization_id + branch_id now. Uses the
    owner's HQ branch as the event's branch by default."""
    owner, org = await setup_org(db_session)
    event = make_event(
        organization_id=org.id,
        branch_id=owner.branch_id,
        created_by=owner.id,
    )
    db_session.add(event)
    await db_session.flush()
    return owner, org, event


# ── Event: creation / required fields ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_id_required(db_session: AsyncSession):
    """organization_id is NOT NULL — there is no platform-wide event
    anymore. Every event belongs to an organization."""
    owner, org = await setup_org(db_session)

    event = make_event(branch_id=owner.branch_id, created_by=owner.id)
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_branch_id_required(db_session: AsyncSession):
    """branch_id is NOT NULL — there is no org-wide (branch-less) event
    anymore. Every event belongs to a specific branch."""
    owner, org = await setup_org(db_session)

    event = make_event(organization_id=org.id, created_by=owner.id)
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_create_branch_level_event(db_session: AsyncSession):
    """organization_id and branch_id both set — the only valid shape now."""
    owner, org = await setup_org(db_session)
    branch = make_branch(org.id, name="Downtown")
    db_session.add(branch)
    await db_session.flush()

    event = make_event(organization_id=org.id, branch_id=branch.id, created_by=owner.id)
    db_session.add(event)
    await db_session.flush()

    assert event.organization_id == org.id
    assert event.branch_id == branch.id


# ── Event: FK constraints ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_delete_cascades_to_events(db_session: AsyncSession):
    """Deleting an organization deletes its events (ondelete=CASCADE) —
    events can no longer survive org deletion since organization_id is
    required."""
    owner, org, event = await setup_event(db_session)
    event_id = event.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_branch_delete_cascades_to_events(db_session: AsyncSession):
    """Deleting a branch deletes its events (ondelete=CASCADE) — events
    can no longer survive branch deletion since branch_id is required.
    Uses a second, user-less branch so the delete isn't blocked by the
    owner's own branch_id FK."""
    owner, org = await setup_org(db_session)
    branch = make_branch(org.id, name="Downtown")
    db_session.add(branch)
    await db_session.flush()

    event = make_event(organization_id=org.id, branch_id=branch.id, created_by=owner.id)
    db_session.add(event)
    await db_session.flush()
    event_id = event.id

    await db_session.delete(branch)
    await db_session.flush()

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_created_by_set_null_on_user_delete(db_session: AsyncSession):
    """created_by is a real FK with SET NULL — event survives, creator link cleared.
    new_owner must have organization_id and branch_id set (both NOT NULL)
    before being assigned as the new org.owner_id."""
    owner, org, event = await setup_event(db_session)
    event_id = event.id

    # new_owner belongs to the same org/branch — required by
    # User.organization_id / User.branch_id being NOT NULL
    new_owner = make_user(
        organization_id=org.id,
        branch_id=owner.branch_id,
        email="new_owner@example.com",
    )
    db_session.add(new_owner)
    await db_session.flush()

    org.owner_id = new_owner.id
    await db_session.flush()

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner.id})
    await db_session.flush()

    # Bypass identity map — the raw DELETE above happened outside the
    # ORM's unit-of-work, so the cached `event` object won't reflect the
    # DB-level SET NULL unless we expire it first.
    db_session.expunge_all()

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    fetched = result.scalar_one()
    assert fetched.created_by is None


# ── Event: relationships ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_organization_relationship(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)
    await db_session.refresh(event, ["organization"])

    assert event.organization is not None
    assert event.organization.id == org.id


@pytest.mark.asyncio
async def test_event_branch_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    branch = make_branch(org.id, name="Downtown")
    db_session.add(branch)
    await db_session.flush()

    event = make_event(organization_id=org.id, branch_id=branch.id, created_by=owner.id)
    db_session.add(event)
    await db_session.flush()
    await db_session.refresh(event, ["branch"])

    assert event.branch is not None
    assert event.branch.id == branch.id


@pytest.mark.asyncio
async def test_organization_events_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    db_session.add(make_event(
        title="Event A", organization_id=org.id, branch_id=owner.branch_id, created_by=owner.id
    ))
    db_session.add(make_event(
        title="Event B", organization_id=org.id, branch_id=owner.branch_id, created_by=owner.id
    ))
    await db_session.flush()
    await db_session.refresh(org, ["events"])

    assert len(org.events) == 2
    titles = {e.title for e in org.events}
    assert titles == {"Event A", "Event B"}


@pytest.mark.asyncio
async def test_branch_events_relationship(db_session: AsyncSession):
    owner, org = await setup_org(db_session)
    branch = make_branch(org.id, name="Downtown")
    db_session.add(branch)
    await db_session.flush()

    db_session.add(make_event(
        title="Branch Event",
        organization_id=org.id,
        branch_id=branch.id,
        created_by=owner.id,
    ))
    await db_session.flush()
    await db_session.refresh(branch, ["events"])

    assert len(branch.events) == 1
    assert branch.events[0].title == "Branch Event"


@pytest.mark.asyncio
async def test_event_creator_relationship(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)
    await db_session.refresh(event, ["creator"])

    assert event.creator is not None
    assert event.creator.id == owner.id


# ── Event: indexes ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_start_date_index_exists(db_session: AsyncSession):
    result = await db_session.execute(text("PRAGMA index_list('events')"))
    index_names = {row[1] for row in result.fetchall()}
    assert "ix_events_org_start_date" in index_names


# ── EventParticipant model ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_participant_persists(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    result = await db_session.execute(
        select(EventParticipant).where(EventParticipant.id == participant.id)
    )
    fetched = result.scalar_one()
    assert fetched.event_id == event.id
    assert fetched.user_id == member.id


@pytest.mark.asyncio
async def test_event_participant_id_is_uuid_format(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    parts = participant.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_event_participant_created_at_auto_set(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    assert participant.created_at is not None


@pytest.mark.asyncio
async def test_event_participant_attended_defaults_false(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    assert participant.attended is False


@pytest.mark.asyncio
async def test_event_participant_attended_can_be_updated(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    participant.attended = True
    await db_session.flush()

    result = await db_session.execute(
        select(EventParticipant).where(EventParticipant.id == participant.id)
    )
    assert result.scalar_one().attended is True


@pytest.mark.asyncio
async def test_event_participant_invited_at_stored(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    invited = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    participant = make_participant(event.id, member.id, invited_at=invited)
    db_session.add(participant)
    await db_session.flush()

    result = await db_session.execute(
        select(EventParticipant).where(EventParticipant.id == participant.id)
    )
    assert result.scalar_one().invited_at is not None


@pytest.mark.asyncio
async def test_event_participant_invited_at_nullable(db_session: AsyncSession):
    """Open-join participants need no invited_at."""
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    assert participant.invited_at is None


@pytest.mark.asyncio
async def test_event_participant_status_stored(db_session: AsyncSession):
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id, status="accepted")
    db_session.add(participant)
    await db_session.flush()

    result = await db_session.execute(
        select(EventParticipant).where(EventParticipant.id == participant.id)
    )
    assert result.scalar_one().status == "accepted"


@pytest.mark.asyncio
async def test_event_participant_status_nullable(db_session: AsyncSession):
    """Status is optional — not every flow sets it up front."""
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    participant = make_participant(event.id, member.id)
    db_session.add(participant)
    await db_session.flush()

    assert participant.status is None


@pytest.mark.asyncio
async def test_event_participant_duplicate_raises(db_session: AsyncSession):
    """A user can only be added to a given event once."""
    owner, org, event = await setup_event(db_session)

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_participant(event.id, member.id))
    await db_session.flush()

    db_session.add(make_participant(event.id, member.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_event_participant_same_user_different_events(db_session: AsyncSession):
    """A user can participate in multiple different events."""
    owner, org = await setup_org(db_session)

    event_a = make_event(
        organization_id=org.id, branch_id=owner.branch_id, created_by=owner.id, title="Morning Run"
    )
    event_b = make_event(
        organization_id=org.id, branch_id=owner.branch_id, created_by=owner.id, title="Evening Yoga"
    )
    db_session.add_all([event_a, event_b])
    await db_session.flush()

    member = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member@example.com")
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_participant(event_a.id, member.id))
    db_session.add(make_participant(event_b.id, member.id))
    await db_session.flush()

    result = await db_session.execute(
        select(EventParticipant).where(EventParticipant.user_id == member.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_event_participant_multiple_users_same_event(db_session: AsyncSession):
    """Multiple users can participate in the same event."""
    owner, org, event = await setup_event(db_session)

    member_a = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member_a@example.com")
    member_b = make_user(organization_id=org.id, branch_id=owner.branch_id, email="member_b@example.com")
    db_session.add_all([member_a, member_b])
    await db_session.flush()

    db_session.add(make_participant(event.id, member_a.id))
    db_session.add(make_participant(event.id, member_b.id))
    await db_session.flush()

    result = await db_session.execute(
        select(EventParticipant).where(EventParticipant.event_id == event.id)
    )
    assert len(result.scalars().all()) == 2