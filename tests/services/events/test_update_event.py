"""
tests/services/events/test_update_event.py

Unit tests for app/services/event/update_event.py

Coverage:
  edit_event()
    Happy path
      - returns an Event instance
      - each field is updated when provided
      - title is stripped of leading/trailing whitespace
      - fields NOT provided (None) are left unchanged
      - valid status transition is persisted

    Validation errors
      - raises 400 when event_status is not a recognized value

    Not-found errors
      - raises 404 when event not found
      - raises 404 when event belongs to a different organization

  cancel_event()
    Happy path
      - returns an Event instance
      - status is set to "cancelled"
      - other fields are left untouched

    Conflict errors
      - raises 409 when event is already cancelled

    Not-found errors
      - raises 404 when event not found
      - raises 404 when event belongs to a different organization
"""

from __future__ import annotations

from datetime import date, time

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Event
from app.services.event.create_event import create_event
from app.services.event.update_event import cancel_event, edit_event
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


def _no_op_edit_kwargs(organization_id: str, event_id: str) -> dict:
    """Minimal edit_event() kwargs where every optional field is None —
    i.e. an edit call that changes nothing, for isolating one field at a time."""
    return dict(
        organization_id=organization_id,
        event_id=event_id,
        title=None,
        description=None,
        image_url=None,
        start_date=None,
        end_date=None,
        recurrence_rule=None,
        event_time=None,
        event_status=None,
    )


# ── edit_event: happy path ──────────────────────────────────────────────────────

class TestEditEventHappyPath:

    @pytest.mark.asyncio
    async def test_returns_event_instance(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "title": "Updated Title"},
        )

        assert isinstance(result, Event)

    @pytest.mark.asyncio
    async def test_title_is_updated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "title": "Staff Retreat"},
        )

        assert result.title == "Staff Retreat"

    @pytest.mark.asyncio
    async def test_title_is_stripped_of_whitespace(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "title": "  Staff Retreat  "},
        )

        assert result.title == "Staff Retreat"

    @pytest.mark.asyncio
    async def test_description_is_updated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "description": "Updated description."},
        )

        assert result.description == "Updated description."

    @pytest.mark.asyncio
    async def test_image_url_is_updated(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr("app.services.event.update_event.confirm_upload", lambda **kwargs: True)
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "image_url": "https://example.com/new-banner.png"},
        )

        assert result.image_url == "https://example.com/new-banner.png"

    @pytest.mark.asyncio
    async def test_start_date_is_updated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "start_date": date(2025, 9, 15)},
        )

        assert result.start_date == date(2025, 9, 15)

    @pytest.mark.asyncio
    async def test_end_date_is_updated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "end_date": date(2025, 9, 20)},
        )

        assert result.end_date == date(2025, 9, 20)

    @pytest.mark.asyncio
    async def test_recurrence_rule_is_updated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "recurrence_rule": "FREQ=WEEKLY;BYDAY=MO"},
        )

        assert result.recurrence_rule == "FREQ=WEEKLY;BYDAY=MO"

    @pytest.mark.asyncio
    async def test_event_time_is_updated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "event_time": time(14, 30)},
        )

        assert result.time == time(14, 30)

    @pytest.mark.asyncio
    async def test_status_is_updated_on_valid_transition(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "event_status": "ongoing"},
        )

        assert result.status == "ongoing"

    @pytest.mark.asyncio
    async def test_fields_not_provided_are_left_unchanged(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(
            db_session, org.id, branch.id, owner.id,
            description="Original description.",
            image_url="https://example.com/original.png",
        )

        result = await edit_event(
            session=db_session,
            **{**_no_op_edit_kwargs(org.id, event.id), "title": "Only Title Changed"},
        )

        assert result.title == "Only Title Changed"
        assert result.description == "Original description."
        assert result.image_url == "https://example.com/original.png"
        assert result.start_date == date(2025, 6, 1)
        assert result.time == time(9, 0)
        assert result.status == "scheduled"


# ── edit_event: validation errors ───────────────────────────────────────────────

class TestEditEventValidation:

    @pytest.mark.asyncio
    async def test_raises_400_on_invalid_status(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await edit_event(
                session=db_session,
                **{**_no_op_edit_kwargs(org.id, event.id), "event_status": "not_a_real_status"},
            )

        assert exc_info.value.status_code == 400


# ── edit_event: not-found errors ────────────────────────────────────────────────

class TestEditEventNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await edit_event(
                session=db_session,
                **{**_no_op_edit_kwargs(org.id, "00000000-0000-0000-0000-000000000000"), "title": "New Title"},
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
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch_org1 = await _make_branch(db_session, org1.id)
        event = await _make_event(db_session, org1.id, branch_org1.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await edit_event(
                session=db_session,
                **{**_no_op_edit_kwargs(org2.id, event.id), "title": "New Title"},
            )

        assert exc_info.value.status_code == 404


# ── cancel_event: happy path ────────────────────────────────────────────────────

class TestCancelEventHappyPath:

    @pytest.mark.asyncio
    async def test_returns_event_instance(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await cancel_event(session=db_session, organization_id=org.id, event_id=event.id)

        assert isinstance(result, Event)

    @pytest.mark.asyncio
    async def test_status_is_set_to_cancelled(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await cancel_event(session=db_session, organization_id=org.id, event_id=event.id)

        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_other_fields_are_untouched(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(
            db_session, org.id, branch.id, owner.id,
            title="Staff Retreat",
            description="A wellness retreat.",
        )

        result = await cancel_event(session=db_session, organization_id=org.id, event_id=event.id)

        assert result.title == "Staff Retreat"
        assert result.description == "A wellness retreat."
        assert result.start_date == date(2025, 6, 1)
        assert result.time == time(9, 0)


# ── cancel_event: conflict errors ───────────────────────────────────────────────

class TestCancelEventConflict:

    @pytest.mark.asyncio
    async def test_raises_409_when_already_cancelled(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        await cancel_event(session=db_session, organization_id=org.id, event_id=event.id)

        with pytest.raises(HTTPException) as exc_info:
            await cancel_event(session=db_session, organization_id=org.id, event_id=event.id)

        assert exc_info.value.status_code == 409


# ── cancel_event: not-found errors ──────────────────────────────────────────────

class TestCancelEventNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await cancel_event(
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
            await cancel_event(session=db_session, organization_id=org2.id, event_id=event.id)

        assert exc_info.value.status_code == 404
