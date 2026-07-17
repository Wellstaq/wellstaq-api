"""
tests/services/events/test_create_event.py

Unit tests for app/services/event/create_event.py

Coverage:
  create_event()
    Happy path
      - returns an Event instance
      - persisted event has correct scalar fields
      - title is stripped of leading/trailing whitespace
      - status is always set to "scheduled" regardless of caller input
      - organization_id and branch_id on the event match the validated inputs
      - optional fields (description, image_url, end_date, recurrence_rule)
        are persisted as None when not provided
      - optional fields are persisted correctly when provided

    Not-found errors
      - raises 404 when organization not found
      - raises 404 when branch not found
      - raises 404 when branch belongs to a different organization
"""

from __future__ import annotations

from datetime import date, time

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Event
from app.services.event.create_event import create_event
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


def _default_kwargs(organization_id: str, branch_id: str, created_by: str) -> dict:
    """Minimal valid kwargs for create_event()."""
    return dict(
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


# ── Happy path ─────────────────────────────────────────────────────────────────

class TestCreateEventHappyPath:

    @pytest.mark.asyncio
    async def test_returns_event_instance(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert isinstance(result, Event)

    @pytest.mark.asyncio
    async def test_event_has_non_null_id_after_creation(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert result.id is not None

    @pytest.mark.asyncio
    async def test_title_is_persisted(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{**_default_kwargs(org.id, branch.id, owner.id), "title": "Staff Retreat"},
        )

        assert result.title == "Staff Retreat"

    @pytest.mark.asyncio
    async def test_title_is_stripped_of_whitespace(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{**_default_kwargs(org.id, branch.id, owner.id), "title": "  Staff Retreat  "},
        )

        assert result.title == "Staff Retreat"

    @pytest.mark.asyncio
    async def test_status_is_always_scheduled(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert result.status == "scheduled"

    @pytest.mark.asyncio
    async def test_organization_id_matches_validated_org(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert result.organization_id == org.id

    @pytest.mark.asyncio
    async def test_branch_id_matches_validated_branch(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert result.branch_id == branch.id

    @pytest.mark.asyncio
    async def test_created_by_is_persisted(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert result.created_by == owner.id

    @pytest.mark.asyncio
    async def test_start_date_is_persisted(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{
                **_default_kwargs(org.id, branch.id, owner.id),
                "start_date": date(2025, 9, 15),
            },
        )

        assert result.start_date == date(2025, 9, 15)

    @pytest.mark.asyncio
    async def test_event_time_is_persisted(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{
                **_default_kwargs(org.id, branch.id, owner.id),
                "event_time": time(14, 30),
            },
        )

        assert result.time == time(14, 30)

    @pytest.mark.asyncio
    async def test_optional_fields_are_none_when_not_provided(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **_default_kwargs(org.id, branch.id, owner.id),
        )

        assert result.description is None
        assert result.image_url is None
        assert result.end_date is None
        assert result.recurrence_rule is None

    @pytest.mark.asyncio
    async def test_description_is_persisted_when_provided(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{
                **_default_kwargs(org.id, branch.id, owner.id),
                "description": "A company-wide annual meeting.",
            },
        )

        assert result.description == "A company-wide annual meeting."

    @pytest.mark.asyncio
    async def test_image_url_is_persisted_when_provided(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{
                **_default_kwargs(org.id, branch.id, owner.id),
                "image_url": "https://example.com/banner.png",
            },
        )

        assert result.image_url == "https://example.com/banner.png"

    @pytest.mark.asyncio
    async def test_end_date_is_persisted_when_provided(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{
                **_default_kwargs(org.id, branch.id, owner.id),
                "start_date": date(2025, 9, 1),
                "end_date": date(2025, 9, 3),
            },
        )

        assert result.end_date == date(2025, 9, 3)

    @pytest.mark.asyncio
    async def test_recurrence_rule_is_persisted_when_provided(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await create_event(
            session=db_session,
            **{
                **_default_kwargs(org.id, branch.id, owner.id),
                "recurrence_rule": "FREQ=WEEKLY;BYDAY=MO",
            },
        )

        assert result.recurrence_rule == "FREQ=WEEKLY;BYDAY=MO"


# ── Not-found errors ───────────────────────────────────────────────────────────

class TestCreateEventNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_organization_not_found(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        with pytest.raises(HTTPException) as exc_info:
            await create_event(
                session=db_session,
                **{
                    **_default_kwargs(org.id, branch.id, owner.id),
                    "organization_id": "00000000-0000-0000-0000-000000000000",
                },
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await create_event(
                session=db_session,
                **{
                    **_default_kwargs(org.id, "00000000-0000-0000-0000-000000000000", owner.id),
                },
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch_org2 = await _make_branch(db_session, org2.id)

        with pytest.raises(HTTPException) as exc_info:
            await create_event(
                session=db_session,
                **_default_kwargs(org1.id, branch_org2.id, owner1.id),
            )

        assert exc_info.value.status_code == 404