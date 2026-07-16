"""
tests/services/events/test_delete_event.py

Unit tests for app/services/event/delete_event.py

Coverage:
  delete_event()
    Happy path
      - returns None
      - permanently removes the Event from the database
      - cascades delete to EventParticipant rows (via DB constraint)
      - logs the deletion action

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

from app.models import Branch, Event, EventParticipant
from app.services.event.create_event import create_event
from app.services.event.delete_event import delete_event
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
    user_id: str,
) -> EventParticipant:
    """Helper to create a participant for cascade delete testing."""
    participant = EventParticipant(
        event_id=event.id,
        user_id=user_id,
    )
    db_session.add(participant)
    await db_session.flush()
    return participant


# ── delete_event: happy path ───────────────────────────────────────────────────

class TestDeleteEventHappyPath:

    @pytest.mark.asyncio
    async def test_returns_none(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await delete_event(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_event_is_permanently_removed(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        event_id = event.id

        await delete_event(
            session=db_session,
            organization_id=org.id,
            event_id=event_id,
        )

        # Verify the event no longer exists in the database
        result = await db_session.execute(
            select(Event).where(Event.id == event_id)
        )
        deleted_event = result.scalar_one_or_none()
        assert deleted_event is None

    @pytest.mark.asyncio
    async def test_cascades_delete_to_event_participants(self, db_session: AsyncSession):
        """
        Verify that deleting an event cascades to EventParticipant rows.
        This relies on the DB constraint ondelete="CASCADE" on the Event relationship.
        """
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        
        # Create participants
        await _make_participant(db_session, event, owner.id)
        
        # Create another user to simulate a second participant
        _, other_user = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="temp-org-for-user"),
            user_kwargs=dict(email="participant@example.com"),
        )
        await _make_participant(db_session, event, other_user.id)

        # Verify participants exist before deletion
        participant_result = await db_session.execute(
            select(EventParticipant).where(EventParticipant.event_id == event.id)
        )
        participants_before = participant_result.scalars().all()
        assert len(participants_before) == 2

        # Delete the event
        await delete_event(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
        )

        # Verify participants are also gone (cascade delete)
        participant_result_after = await db_session.execute(
            select(EventParticipant).where(EventParticipant.event_id == event.id)
        )
        participants_after = participant_result_after.scalars().all()
        assert len(participants_after) == 0

    @pytest.mark.asyncio
    async def test_logs_deletion_action(self, db_session: AsyncSession, caplog):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with caplog.at_level("INFO"):
            await delete_event(
                session=db_session,
                organization_id=org.id,
                event_id=event.id,
            )

        assert "Event deleted" in caplog.text
        assert str(event.id) in caplog.text
        assert org.id in caplog.text


# ── delete_event: not-found errors ─────────────────────────────────────────────

class TestDeleteEventNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await delete_event(
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
            await delete_event(
                session=db_session,
                organization_id=org2.id,
                event_id=event.id,
            )

        assert exc_info.value.status_code == 404
        assert "Event not found" in str(exc_info.value.detail)