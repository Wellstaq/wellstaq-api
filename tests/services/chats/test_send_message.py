"""
tests/services/chat/test_send_message.py

Unit tests for app/services/chat/send_message.py

Coverage:
  send_message()
    Happy path
      - creates message with content only
      - creates message with media only
      - creates message with both content and media
      - returns GroupChatMessage instance
      - strips whitespace from content
      - sets is_pinned=False by default
      - logs message sent with correct metadata

    Validation errors
      - raises 400 when both content and media_url are None
      - raises 400 when both content and media_url are empty/whitespace

    Not-found errors
      - propagates 404 from validate_conversation for invalid club
      - propagates 404 from validate_conversation for invalid event
      - propagates 404 for different organization

    Permission errors
      - propagates 403 from require_conversation_membership for non-member
"""

from __future__ import annotations

from datetime import date, time, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Event, GroupChatMessage, SubscriptionPlan, OrganizationSubscription
from app.services.chat.send_message import send_message
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_plan(db_session: AsyncSession, **overrides) -> SubscriptionPlan:
    """Create a subscription plan with generous limits for testing."""
    defaults = dict(
        name="Test Plan",
        billing_interval="monthly",
        amount=0,
        currency="USD",
        features={"max_clubs": None},  # Unlimited clubs for testing
    )
    plan = SubscriptionPlan(**{**defaults, **overrides})
    db_session.add(plan)
    await db_session.flush()
    return plan


async def _make_subscription(
    db_session: AsyncSession,
    organization_id: str,
    plan_id: str,
    **overrides
) -> OrganizationSubscription:
    """Create an active subscription for an organization."""
    defaults = dict(
        status="active",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    sub = OrganizationSubscription(
        organization_id=organization_id,
        plan_id=plan_id,
        **{**defaults, **overrides}
    )
    db_session.add(sub)
    await db_session.flush()
    return sub


async def _make_org_with_subscription(db_session: AsyncSession, **org_owner_kwargs):
    """make_org_with_owner() plus an active subscription, required by
    create_club()'s plan-limit check."""
    org, owner = await make_org_with_owner(db_session, **org_owner_kwargs)
    plan = await _make_plan(db_session)
    await _make_subscription(db_session, org.id, plan.id)
    return org, owner


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


def _mock_current_user(user_id: str) -> MagicMock:
    """Create a mock CurrentUser for dependency injection."""
    mock = MagicMock()
    mock.user_id = user_id
    mock.permissions = []
    mock.is_super_admin = False
    return mock


# ── send_message: happy path ────────────────────────────────────────────────

class TestSendMessageHappyPath:

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_creates_message_with_content_only(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            content="Hello everyone!",
            media_url=None,
            media_type=None,
        )

        assert isinstance(result, GroupChatMessage)
        assert result.content == "Hello everyone!"
        assert result.media_url is None
        assert result.media_type is None

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_creates_message_with_media_only(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            content=None,
            media_url="https://example.com/image.png",
            media_type="image/png",
        )

        assert result.media_url == "https://example.com/image.png"
        assert result.media_type == "image/png"
        assert result.content is None

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_creates_message_with_both_content_and_media(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            content="Check out this photo!",
            media_url="https://example.com/photo.jpg",
            media_type="image/jpeg",
        )

        assert result.content == "Check out this photo!"
        assert result.media_url == "https://example.com/photo.jpg"
        assert result.media_type == "image/jpeg"

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_returns_group_chat_message_instance(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            content="Test message",
            media_url=None,
            media_type=None,
        )

        assert isinstance(result, GroupChatMessage)
        assert result.id is not None
        assert result.organization_id == org.id
        assert result.conversation_type == "club"
        assert result.conversation_id == club.id
        assert result.user_id == owner.id

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_strips_whitespace_from_content(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            content="  Hello with whitespace  ",
            media_url=None,
            media_type=None,
        )

        assert result.content == "Hello with whitespace"

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_sets_is_pinned_false_by_default(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
            current_user=current_user,
            content="Regular message",
            media_url=None,
            media_type=None,
        )

        assert result.is_pinned is False

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_works_for_event_conversations(
        self, mock_validate, mock_require_membership, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await send_message(
            session=db_session,
            organization_id=org.id,
            conversation_type="event",
            conversation_id=event.id,
            current_user=current_user,
            content="Event message",
            media_url=None,
            media_type=None,
        )

        assert result.conversation_type == "event"
        assert result.conversation_id == event.id

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_logs_message_sent_with_correct_metadata(
        self, mock_validate, mock_require_membership, db_session: AsyncSession, caplog
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        current_user = _mock_current_user(owner.id)

        with caplog.at_level("INFO"):
            result = await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id=club.id,
                current_user=current_user,
                content="Logged message",
                media_url=None,
                media_type=None,
            )

        assert "Group chat message sent" in caplog.text
        assert str(result.id) in caplog.text
        assert "club" in caplog.text
        assert str(club.id) in caplog.text


# ── send_message: validation errors ─────────────────────────────────────────

class TestSendMessageValidationErrors:

    @pytest.mark.asyncio
    async def test_raises_400_when_both_content_and_media_are_none(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        current_user = _mock_current_user("user-123")

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="any-id",
                current_user=current_user,
                content=None,
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "content or media" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_raises_400_when_content_is_whitespace_only(
        self, db_session: AsyncSession
    ):
        """Whitespace-only content becomes None after strip, leaving no content."""
        org, _ = await make_org_with_owner(db_session)
        current_user = _mock_current_user("user-123")

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="any-id",
                current_user=current_user,
                content="   ",
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_raises_400_when_content_empty_string_and_no_media(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        current_user = _mock_current_user("user-123")

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="any-id",
                current_user=current_user,
                content="",
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


# ── send_message: not-found errors ──────────────────────────────────────────

class TestSendMessageNotFoundErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_propagates_404_for_invalid_club(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found in this organization.",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="invalid-club-id",
                current_user=current_user,
                content="Hello",
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_propagates_404_for_invalid_event(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found in this organization.",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="event",
                conversation_id="invalid-event-id",
                current_user=current_user,
                content="Hello",
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_propagates_404_for_different_organization(
        self, mock_validate, db_session: AsyncSession
    ):
        mock_validate.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found in this organization.",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="club-from-other-org",
                current_user=current_user,
                content="Hello",
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# ── send_message: permission errors ─────────────────────────────────────────

class TestSendMessagePermissionErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.send_message.require_conversation_membership")
    @patch("app.services.chat.send_message.validate_conversation")
    async def test_propagates_403_when_not_a_member(
        self, mock_require_membership, mock_validate, db_session: AsyncSession
    ):
        mock_validate.return_value = AsyncMock()
        mock_require_membership.side_effect = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this conversation",
        )

        org, owner = await make_org_with_owner(db_session)
        current_user = _mock_current_user(owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="club-id",
                current_user=current_user,
                content="Hello",
                media_url=None,
                media_type=None,
            )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN