"""
tests/services/events/test_get_event.py

Unit tests for app/services/event/get_event.py

Coverage:
  get_event()
    Happy path
      - returns (Event, participant_count)
      - returns event belonging to the specified organization

    Not-found errors
      - raises 404 when event not found
      - raises 404 when event belongs to a different organization

  get_events()
    Happy path
      - returns (list of (Event, participant_count) pairs, total count)
      - returns empty list and 0 count when no events exist
      - returns only events belonging to the specified organization
      - filters by branch_id when provided
      - respects pagination (offset and limit)
      - returns correct total count even when paginated
      - orders events by start_date descending

    Not-found errors
      - raises 404 is NOT raised for empty results (returns empty list)

  get_event_participants()
    Happy path
      - returns a tuple of (list of (participant, user) pairs, total count)
      - returns empty list and 0 count when no participants exist
      - returns correct total count even when paginated
      - respects pagination (offset and limit)
      - joins with User table to provide user details
      - orders participants by user name (first_name, last_name)

    Not-found errors
      - raises 404 when event not found
      - raises 404 when event belongs to a different organization
"""

from __future__ import annotations

from datetime import date, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Event, EventParticipant, User
from app.services.event.create_event import create_event
from app.services.event.get_event import get_event, get_events, get_event_participants
from tests.conftest import make_org_with_owner


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Lagos Branch",
) -> Branch:
    branch = Branch(
        organization_id=organization_id,
        name=name,
    )
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_event(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    created_by: str,
    **overrides,
) -> Event:
    kwargs = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        title="Annual General Meeting",
        description=None,
        image_url=None,
        start_date=date(2025, 6, 1),
        end_date=None,
        recurrence_rule=None,
        event_time=time(9, 0),
        created_by=created_by,
    )
    kwargs.update(overrides)
    return await create_event(session=db_session, **kwargs)


async def _make_participant(
    db_session: AsyncSession,
    event: Event,
    user: User,
) -> EventParticipant:
    """Helper to create a participant for an event."""
    participant = EventParticipant(
        event_id=event.id,
        user_id=user.id,
    )
    db_session.add(participant)
    await db_session.flush()
    return participant


# ── get_event: happy path ──────────────────────────────────────────────────────

class TestGetEventHappyPath:

    @pytest.mark.asyncio
    async def test_returns_event_instance(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result, participant_count = await get_event(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert isinstance(result, Event)
        assert participant_count == 0
        assert result.id == event.id
        assert result.title == "Annual General Meeting"

    @pytest.mark.asyncio
    async def test_returns_event_belonging_to_specified_organization(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Verify we can retrieve the event with the correct org ID
        result, participant_count = await get_event(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert result.organization_id == org.id


# ── get_event: not-found errors ───────────────────────────────────────────────

class TestGetEventNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_event(
                session=db_session,
                organization_id=org.id,
                event_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404
        assert "Event not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_event_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch_org1 = await _make_branch(db_session, org1.id)
        event = await _make_event(db_session, org1.id, branch_org1.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_event(
                session=db_session,
                organization_id=org2.id,
                event_id=event.id,
            )

        assert exc_info.value.status_code == 404
        assert "Event not found" in str(exc_info.value.detail)


# ── get_events: happy path ─────────────────────────────────────────────────────

class TestGetEventsHappyPath:

    @pytest.mark.asyncio
    async def test_returns_tuple_with_list_and_count(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_event(db_session, org.id, branch.id, owner.id)

        result, count = await get_events(
            session=db_session,
            organization_id=org.id,
        )

        assert isinstance(result, list)
        assert isinstance(count, int)
        assert count == 1
        assert len(result) == 1
        assert isinstance(result[0][0], Event)
        assert result[0][1] == 0

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_events_exist(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        result, count = await get_events(
            session=db_session,
            organization_id=org.id,
        )

        assert result == []
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_only_events_for_specified_organization(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch1 = await _make_branch(db_session, org1.id)
        branch2 = await _make_branch(db_session, org2.id)

        await _make_event(db_session, org1.id, branch1.id, owner1.id)
        await _make_event(db_session, org2.id, branch2.id, owner2.id)

        # Get events for org1
        result1, count1 = await get_events(session=db_session, organization_id=org1.id)
        assert count1 == 1
        assert result1[0][0].organization_id == org1.id

        # Get events for org2
        result2, count2 = await get_events(session=db_session, organization_id=org2.id)
        assert count2 == 1
        assert result2[0][0].organization_id == org2.id

    @pytest.mark.asyncio
    async def test_filters_by_branch_id_when_provided(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch1 = await _make_branch(db_session, org.id, name="Branch One")
        branch2 = await _make_branch(db_session, org.id, name="Branch Two")

        event1 = await _make_event(db_session, org.id, branch1.id, owner.id)
        event2 = await _make_event(db_session, org.id, branch2.id, owner.id)

        # Get events for branch1 only
        result, count = await get_events(
            session=db_session,
            organization_id=org.id,
            branch_id=branch1.id,
        )

        assert count == 1
        assert len(result) == 1
        assert result[0][0].id == event1.id
        assert result[0][0].branch_id == branch1.id

    @pytest.mark.asyncio
    async def test_respects_pagination_limit(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        # Create 5 events
        for i in range(5):
            await _make_event(
                db_session,
                org.id,
                branch.id,
                owner.id,
                title=f"Event {i}",
                start_date=date(2025, 6, 1),
            )

        # Request only 2
        result, count = await get_events(
            session=db_session,
            organization_id=org.id,
            limit=2,
        )

        assert count == 5  # Total count is accurate
        assert len(result) == 2  # But only 2 returned

    @pytest.mark.asyncio
    async def test_respects_pagination_offset(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        # Create events with unique titles
        titles = ["Event A", "Event B", "Event C", "Event D", "Event E"]
        for i, title in enumerate(titles):
            await _make_event(
                db_session,
                org.id,
                branch.id,
                owner.id,
                title=title,
                start_date=date(2025, 6, 1),
            )

        # Get first page
        result1, count1 = await get_events(
            session=db_session,
            organization_id=org.id,
            limit=2,
            offset=0,
        )

        # Get second page
        result2, count2 = await get_events(
            session=db_session,
            organization_id=org.id,
            limit=2,
            offset=2,
        )

        assert count1 == 5
        assert count2 == 5
        assert len(result1) == 2
        assert len(result2) == 2

        # Ensure pages contain different data
        titles_page1 = [e.title for e, _count in result1]
        titles_page2 = [e.title for e, _count in result2]
        assert set(titles_page1) != set(titles_page2)
        assert "Event A" not in titles_page2  # Should be on page 1 only

    @pytest.mark.asyncio
    async def test_orders_events_by_start_date_descending(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        # Create events with different dates
        dates = [
            date(2025, 6, 1),
            date(2025, 8, 1),
            date(2025, 4, 1),
        ]
        for i, d in enumerate(dates):
            await _make_event(
                db_session,
                org.id,
                branch.id,
                owner.id,
                title=f"Event {i}",
                start_date=d,
            )

        result, count = await get_events(session=db_session, organization_id=org.id)

        assert count == 3
        assert len(result) == 3

        # Verify descending order (latest first)
        returned_dates = [e.start_date for e, _count in result]
        assert returned_dates == sorted(returned_dates, reverse=True)


# ── get_events: not-found errors ──────────────────────────────────────────────

class TestGetEventsNotFound:

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_org_with_no_events(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        result, count = await get_events(
            session=db_session,
            organization_id=org.id,
        )

        assert result == []
        assert count == 0


# ── get_event_participants: happy path ────────────────────────────────────────

class TestGetEventParticipantsHappyPath:

    @pytest.mark.asyncio
    async def test_returns_tuple_with_list_and_count(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Add a participant
        await _make_participant(db_session, event, owner)

        result, count = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert isinstance(result, list)
        assert isinstance(count, int)
        assert count == 1
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_participants(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result, count = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert result == []
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_correct_total_count_with_pagination(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Create multiple users and add them as participants
        users = []
        for i in range(5):
            _, user = await make_org_with_owner(
                db_session,
                org_kwargs=dict(slug=f"temp-org-{i}"),
                user_kwargs=dict(
                    email=f"user{i}@example.com",
                    first_name=f"User{i}",
                    last_name=f"Last{i}",
                ),
            )
            users.append(user)
            await _make_participant(db_session, event, user)

        result, count = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            limit=2,
            offset=0,
        )

        assert count == 5  # Total count is accurate
        assert len(result) == 2  # But only limited results returned

    @pytest.mark.asyncio
    async def test_respects_pagination_offset(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Create users
        users = []
        for i in range(5):
            _, user = await make_org_with_owner(
                db_session,
                org_kwargs=dict(slug=f"temp-org-{i}"),
                user_kwargs=dict(
                    email=f"user{i}@example.com",
                    first_name=f"User{i}",
                    last_name=f"Last{i}",
                ),
            )
            users.append(user)
            await _make_participant(db_session, event, user)

        # Get first page
        result1, count1 = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            limit=2,
            offset=0,
        )

        # Get second page
        result2, count2 = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            limit=2,
            offset=2,
        )

        assert count1 == 5
        assert count2 == 5
        assert len(result1) == 2
        assert len(result2) == 2

    @pytest.mark.asyncio
    async def test_joins_with_user_table(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        await _make_participant(db_session, event, owner)

        result, count = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert len(result) == 1
        participant_record, user_record = result[0]

        assert isinstance(participant_record, EventParticipant)
        assert isinstance(user_record, User)
        assert participant_record.event_id == event.id
        assert participant_record.user_id == user_record.id

    @pytest.mark.asyncio
    async def test_orders_participants_by_user_name(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Create users with names that will be sorted
        users_data = [
            ("Zebra", "Zlast", "zebra@example.com"),
            ("Alice", "Alast", "alice@example.com"),
            ("Bob", "Blast", "bob@example.com"),
        ]

        for first, last, email in users_data:
            _, user = await make_org_with_owner(
                db_session,
                org_kwargs=dict(slug=f"temp-{email}"),
                user_kwargs=dict(
                    email=email,
                    first_name=first,
                    last_name=last,
                ),
            )
            await _make_participant(db_session, event, user)

        result, count = await get_event_participants(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert count == 3
        assert len(result) == 3

        # Verify ordering (by first_name, then last_name)
        returned_users = [item[1] for item in result]
        returned_names = [(u.first_name, u.last_name) for u in returned_users]
        expected_order = sorted(users_data, key=lambda x: (x[0], x[1]))
        expected_names = [(f, l) for f, l, _ in expected_order]

        assert returned_names == expected_names


# ── get_event_participants: not-found errors ─────────────────────────────────

class TestGetEventParticipantsNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_event_participants(
                session=db_session,
                organization_id=org.id,
                event_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_event_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch_org1 = await _make_branch(db_session, org1.id)
        event = await _make_event(db_session, org1.id, branch_org1.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_event_participants(
                session=db_session,
                organization_id=org2.id,
                event_id=event.id,
            )

        assert exc_info.value.status_code == 404
