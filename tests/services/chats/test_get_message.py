"""
tests/services/chat/test_get_messages.py

Unit tests for app/services/chat/get_messages.py

Coverage:
  get_messages()
    Happy path
      - returns tuple of (messages, total count)
      - returns messages ordered by created_at descending
      - returns empty list and 0 total when no messages
      - returns only messages for specified conversation
      - respects pagination (offset and limit)
      - total reflects full count, not page size

    Validation errors
      - propagates 400 from validate_conversation for invalid type

    Not-found errors
      - propagates 404 from validate_conversation when club not found
      - propagates 404 from validate_conversation when event not found
      - propagates 404 from validate_conversation for different org

    Permission errors
      - propagates 403 from require_conversation_membership when not member
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Event, GroupChatMessage
from app.services.chat.get_messages import get_messages
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Main Branch",
) -> Branch:
    from app.models import Branch

    branch = Branch(
        organization_id=organization_id,
        name=name,
    )
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_club(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    leader_id: str,
    name: str = "Running Club",
    privacy: str = "public",
) -> Club:
    from app.services.club.create_club import create_club

    return await create_club(
        session=db_session,
        organization_id=organization_id,
        branch_id=branch_id,
        name=name,
        description=None,
        privacy=privacy,
        created_by=leader_id,
    )


async def _make_event(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    created_by: str,
    **overrides,
) -> Event:
    from app.services.event.create_event import create_event

    kwargs = dict(
        organization_id=organization_id,
        branch_id=branch_id,
        title="Community Meetup",
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


async def _make_message(
    db_session: AsyncSession,
    organization_id: str,
    conversation_type: str,
    conversation_id: str,
    sender_id: str,
    content: str = "Hello world",
) -> GroupChatMessage:
    message = GroupChatMessage(
        organization_id=organization_id,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        user_id=sender_id,
        content=content,
    )
    db_session.add(message)
    await db_session.flush()
    await db_session.refresh(message)
    return message


def _mock_current_user(user_id: str) -> MagicMock:
    """Create a mock CurrentUser for dependency injection."""
    mock = MagicMock()
    mock.user_id = user_id
    mock.permissions = []
    mock.is_super_admin = False
    return mock


# ── get_messages: happy path ─────────────────────────────────────────────────

class TestGetMessagesHappyPath:

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_returns_tuple_with_messages_and_total(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        messages, total = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
        )

        assert isinstance(messages, list)
        assert isinstance(total, int)
        assert total == 1
        assert len(messages) == 1
        assert messages[0].id == message.id

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_returns_messages_ordered_by_created_at_descending(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Create messages in chronological order
        msg1 = await _make_message(db_session, org.id, "club", club.id, owner.id, "First")
        msg2 = await _make_message(db_session, org.id, "club", club.id, owner.id, "Second")
        msg3 = await _make_message(db_session, org.id, "club", club.id, owner.id, "Third")

        current_user = _mock_current_user(owner.id)

        messages, total = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
        )

        assert total == 3
        # Should be newest first
        contents = [m.content for m in messages]
        assert contents == ["Third", "Second", "First"]

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_returns_empty_list_and_zero_total_when_no_messages(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        messages, total = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
        )

        assert messages == []
        assert total == 0

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_returns_only_messages_for_specified_conversation(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club_a = await _make_club(db_session, org.id, branch.id, owner.id, name="Club A")
        club_b = await _make_club(db_session, org.id, branch.id, owner.id, name="Club B")

        msg_a = await _make_message(db_session, org.id, "club", club_a.id, owner.id, "In A")
        await _make_message(db_session, org.id, "club", club_b.id, owner.id, "In B")

        current_user = _mock_current_user(owner.id)

        messages, total = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club_a.id,
            current_user=current_user,
        )

        assert total == 1
        assert len(messages) == 1
        assert messages[0].content == "In A"

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_respects_pagination_limit(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Create 5 messages
        for i in range(5):
            await _make_message(db_session, org.id, "club", club.id, owner.id, f"Message {i}")

        current_user = _mock_current_user(owner.id)

        messages, total = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            limit=2,
        )

        assert total == 5
        assert len(messages) == 2

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_respects_pagination_offset(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Create messages with identifiable content
        for i in range(5):
            await _make_message(db_session, org.id, "club", club.id, owner.id, f"Message {i}")

        current_user = _mock_current_user(owner.id)

        # Get first page
        messages_1, total_1 = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            limit=2,
            offset=0,
        )

        # Get second page
        messages_2, total_2 = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            limit=2,
            offset=2,
        )

        assert total_1 == total_2 == 5
        assert len(messages_1) == 2
        assert len(messages_2) == 2

        # Ensure different messages
        ids_1 = {m.id for m in messages_1}
        ids_2 = {m.id for m in messages_2}
        assert ids_1.isdisjoint(ids_2)

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_works_for_event_conversations(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        message = await _make_message(db_session, org.id, "event", event.id, owner.id)

        current_user = _mock_current_user(owner.id)

        messages, total = await get_messages(
            session=db_session,
            organization_id=org.id,
            conversation_type="event",
            conversation_id=event.id,
            current_user=current_user,
        )

        assert total == 1
        assert len(messages) == 1
        assert messages[0].id == message.id


# ── get_messages: validation errors ──────────────────────────────────────────

class TestGetMessagesValidationErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_propagates_400_from_validate_conversation(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="conversation_type must be one of ['club', 'event']",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_messages(
                session=db_session,
                organization_id=org.id,
                conversation_type="invalid",
                conversation_id="any-id",
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


# ── get_messages: not-found errors ────────────────────────────────────────────

class TestGetMessagesNotFoundErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_propagates_404_when_club_not_found(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found in this organization.",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_messages(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="00000000-0000-0000-0000-000000000000",
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_propagates_404_when_event_not_found(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found in this organization.",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_messages(
                session=db_session,
                organization_id=org.id,
                conversation_type="event",
                conversation_id="00000000-0000-0000-0000-000000000000",
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_propagates_404_for_different_org(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found in this organization.",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_messages(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="some-club-id",
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# ── get_messages: permission errors ───────────────────────────────────────────

class TestGetMessagesPermissionErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.get_messages.require_conversation_membership")
    @patch("app.services.chat.get_messages.validate_conversation")
    async def test_propagates_403_when_not_a_member(
        self, mock_require_membership, mock_validate, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.side_effect = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this club",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_messages(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="club-id",
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN