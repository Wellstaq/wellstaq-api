"""
tests/services/events/test_participants.py

Unit tests for app/services/event/participants.py

Coverage:
  join_event()
    Happy path
      - returns an EventParticipant with correct fields (self-join)
      - sets invited_at and status="invited" when is_invite=True
      - sets invited_at=None and status from initial_status when is_invite=False
      - default initial_status is "pending"

    Not-found / permission errors
      - raises 404 when event not found
      - raises 404 when event belongs to a different organization
      - raises 404 when user not found
      - raises 404 when user belongs to a different organization
      - raises 403 when user's branch differs from event's branch
      - raises 409 when user is already a participant

  update_participant_status()
    Happy path
      - returns updated EventParticipant with new status
      - persists the status change

    Errors
      - raises 404 when event not found
      - raises 400 when status value is invalid
      - raises 404 when user is not a participant

  mark_attendance()
    Happy path
      - returns EventParticipant with attended=True
      - returns EventParticipant with attended=False (un-marking)

    Errors
      - raises 404 when event not found
      - raises 404 when user is not a participant

  remove_participant()
    Happy path
      - returns None
      - permanently removes the EventParticipant row

    Errors
      - raises 404 when event not found
      - raises 404 when user is not a participant
"""

from __future__ import annotations

from datetime import date, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Event, EventParticipant, User
from app.services.event.create_event import create_event
from app.services.event.participant import (
    join_event,
    mark_attendance,
    remove_participant,
    update_participant_status,
)
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


async def _make_user_in_branch(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    email: str,
) -> User:
    """Create a user assigned to a specific branch."""
    user = User(
        first_name = "test",
        last_name = "user",
        organization_id=organization_id,
        branch_id=branch_id,
        email=email,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _join(
    db_session: AsyncSession,
    organization_id: str,
    event_id: str,
    user_id: str,
    **kwargs,
) -> EventParticipant:
    """Shortcut to join an event."""
    return await join_event(
        session=db_session,
        organization_id=organization_id,
        event_id=event_id,
        user_id=user_id,
        **kwargs,
    )


# ── join_event: happy path ─────────────────────────────────────────────────────

class TestJoinEventHappyPath:

    @pytest.mark.asyncio
    async def test_returns_event_participant(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Assign owner to the event's branch
        owner.branch_id = branch.id
        await db_session.flush()

        participant = await _join(db_session, org.id, event.id, owner.id)

        assert isinstance(participant, EventParticipant)
        assert participant.event_id == event.id
        assert participant.user_id == owner.id

    @pytest.mark.asyncio
    async def test_invite_sets_invited_at_and_status(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        participant = await _join(db_session, org.id, event.id, owner.id, is_invite=True)

        assert participant.status == "invited"
        assert participant.invited_at is not None

    @pytest.mark.asyncio
    async def test_self_join_sets_invited_at_none(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        participant = await _join(db_session, org.id, event.id, owner.id, is_invite=False)

        assert participant.invited_at is None

    @pytest.mark.asyncio
    async def test_default_initial_status_is_pending(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        participant = await _join(db_session, org.id, event.id, owner.id)

        assert participant.status == "pending"

    @pytest.mark.asyncio
    async def test_custom_initial_status_is_respected(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        participant = await _join(
            db_session, org.id, event.id, owner.id, initial_status="accepted"
        )

        assert participant.status == "accepted"

    @pytest.mark.asyncio
    async def test_attended_defaults_to_false(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        participant = await _join(db_session, org.id, event.id, owner.id)

        assert participant.attended is False

    @pytest.mark.asyncio
    async def test_logs_join_action(self, db_session: AsyncSession, caplog):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        with caplog.at_level("INFO"):
            await _join(db_session, org.id, event.id, owner.id)

        assert "User joined event" in caplog.text
        assert str(event.id) in caplog.text
        assert str(owner.id) in caplog.text


# ── join_event: errors ─────────────────────────────────────────────────────────

class TestJoinEventErrors:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await _join(
                db_session,
                org.id,
                "00000000-0000-0000-0000-000000000000",
                owner.id,
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
        branch = await _make_branch(db_session, org1.id)
        event = await _make_event(db_session, org1.id, branch.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await _join(db_session, org2.id, event.id, owner1.id)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _join(
                db_session,
                org.id,
                event.id,
                "00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404
        assert "User not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_user_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-a"),
            user_kwargs=dict(email="owner-a@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-b"),
            user_kwargs=dict(email="owner-b@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)
        event = await _make_event(db_session, org1.id, branch.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            # owner2 belongs to org2, but we're querying against org1
            await _join(db_session, org1.id, event.id, owner2.id)

        assert exc_info.value.status_code == 404
        assert "User not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_403_when_user_branch_differs_from_event_branch(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        event_branch = await _make_branch(db_session, org.id, name="Event Branch")
        user_branch = await _make_branch(db_session, org.id, name="User Branch")
        event = await _make_event(db_session, org.id, event_branch.id, owner.id)

        other_user = await _make_user_in_branch(
            db_session, org.id, user_branch.id, "outsider@example.com"
        )

        with pytest.raises(HTTPException) as exc_info:
            await _join(db_session, org.id, event.id, other_user.id)

        assert exc_info.value.status_code == 403
        assert "branch" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_raises_409_when_user_already_a_participant(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()

        await _join(db_session, org.id, event.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _join(db_session, org.id, event.id, owner.id)

        assert exc_info.value.status_code == 409
        assert "already a participant" in str(exc_info.value.detail)


# ── update_participant_status: happy path ──────────────────────────────────────

class TestUpdateParticipantStatusHappyPath:

    @pytest.mark.asyncio
    async def test_returns_updated_participant(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        participant = await update_participant_status(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            new_status="accepted",
        )

        assert isinstance(participant, EventParticipant)
        assert participant.status == "accepted"

    @pytest.mark.asyncio
    async def test_persists_status_change(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        await update_participant_status(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            new_status="declined",
        )

        result = await db_session.execute(
            select(EventParticipant).where(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == owner.id,
            )
        )
        stored = result.scalar_one_or_none()
        assert stored is not None
        assert stored.status == "declined"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["invited", "accepted", "declined", "pending"])
    async def test_all_valid_statuses_are_accepted(
        self, db_session: AsyncSession, status: str
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        participant = await update_participant_status(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            new_status=status,
        )

        assert participant.status == status


# ── update_participant_status: errors ─────────────────────────────────────────

class TestUpdateParticipantStatusErrors:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await update_participant_status(
                session=db_session,
                organization_id=org.id,
                event_id="00000000-0000-0000-0000-000000000000",
                user_id=owner.id,
                new_status="accepted",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_400_when_status_is_invalid(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await update_participant_status(
                session=db_session,
                organization_id=org.id,
                event_id=event.id,
                user_id=owner.id,
                new_status="not_a_real_status",
            )

        assert exc_info.value.status_code == 400
        assert "Invalid status" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_a_participant(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await update_participant_status(
                session=db_session,
                organization_id=org.id,
                event_id=event.id,
                user_id=owner.id,
                new_status="accepted",
            )

        assert exc_info.value.status_code == 404
        assert "not a participant" in str(exc_info.value.detail)


# ── mark_attendance: happy path ────────────────────────────────────────────────

class TestMarkAttendanceHappyPath:

    @pytest.mark.asyncio
    async def test_marks_attended_true(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        participant = await mark_attendance(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            attended=True,
        )

        assert participant.attended is True

    @pytest.mark.asyncio
    async def test_marks_attended_false(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        # Mark as attended first
        await mark_attendance(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            attended=True,
        )

        # Un-mark attendance
        participant = await mark_attendance(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            attended=False,
        )

        assert participant.attended is False

    @pytest.mark.asyncio
    async def test_persists_attendance_change(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        await mark_attendance(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
            attended=True,
        )

        result = await db_session.execute(
            select(EventParticipant).where(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == owner.id,
            )
        )
        stored = result.scalar_one_or_none()
        assert stored is not None
        assert stored.attended is True


# ── mark_attendance: errors ────────────────────────────────────────────────────

class TestMarkAttendanceErrors:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await mark_attendance(
                session=db_session,
                organization_id=org.id,
                event_id="00000000-0000-0000-0000-000000000000",
                user_id=owner.id,
                attended=True,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_a_participant(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await mark_attendance(
                session=db_session,
                organization_id=org.id,
                event_id=event.id,
                user_id=owner.id,
                attended=True,
            )

        assert exc_info.value.status_code == 404
        assert "not a participant" in str(exc_info.value.detail)


# ── remove_participant: happy path ─────────────────────────────────────────────

class TestRemoveParticipantHappyPath:

    @pytest.mark.asyncio
    async def test_returns_none(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        result = await remove_participant(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_participant_row_is_permanently_removed(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org.id, event.id, owner.id)

        await remove_participant(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
        )

        result = await db_session.execute(
            select(EventParticipant).where(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == owner.id,
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_only_removes_targeted_participant(self, db_session: AsyncSession):
        """Removing one participant must not affect others in the same event."""
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        owner.branch_id = branch.id

        other_user = await _make_user_in_branch(
            db_session, org.id, branch.id, "other@example.com"
        )
        await db_session.flush()

        await _join(db_session, org.id, event.id, owner.id)
        await _join(db_session, org.id, event.id, other_user.id)

        await remove_participant(
            session=db_session,
            organization_id=org.id,
            event_id=event.id,
            user_id=owner.id,
        )

        result = await db_session.execute(
            select(EventParticipant).where(EventParticipant.event_id == event.id)
        )
        remaining = result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].user_id == other_user.id


# ── remove_participant: errors ─────────────────────────────────────────────────

class TestRemoveParticipantErrors:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await remove_participant(
                session=db_session,
                organization_id=org.id,
                event_id="00000000-0000-0000-0000-000000000000",
                user_id=owner.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_a_participant(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await remove_participant(
                session=db_session,
                organization_id=org.id,
                event_id=event.id,
                user_id=owner.id,
            )

        assert exc_info.value.status_code == 404
        assert "not a participant" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_event_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-x"),
            user_kwargs=dict(email="owner-x@example.com"),
        )
        org2, _owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-y"),
            user_kwargs=dict(email="owner-y@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)
        event = await _make_event(db_session, org1.id, branch.id, owner1.id)
        owner1.branch_id = branch.id
        await db_session.flush()
        await _join(db_session, org1.id, event.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await remove_participant(
                session=db_session,
                organization_id=org2.id,
                event_id=event.id,
                user_id=owner1.id,
            )

        assert exc_info.value.status_code == 404