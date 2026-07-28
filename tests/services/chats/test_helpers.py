"""
tests/services/chat/test_helpers.py

Unit tests for app/services/chat/_helpers.py

Coverage:
  validate_conversation()
    Happy path
      - returns None for valid club conversation
      - returns None for valid event conversation

    Validation errors
      - raises 400 when conversation_type is invalid

    Not-found errors
      - raises 404 when club not found
      - raises 404 when club belongs to different organization
      - raises 404 when event not found
      - raises 404 when event belongs to different organization

  require_conversation_membership()
    Happy path
      - delegates to require_club_membership for club type
      - delegates to require_event_participation for event type

  get_message_or_404()
    Happy path
      - returns GroupChatMessage when found

    Not-found errors
      - raises 404 when message not found
      - raises 404 when message belongs to different organization

  get_conversation_branch_id()
    Happy path
      - returns branch_id for club conversation
      - returns branch_id for event conversation

    Not-found errors
      - raises 404 when club not found
      - raises 404 when event not found

  require_chat_moderation_access()
    Happy path
      - grants access to club leader (no permission needed)
      - grants access via chat.moderate permission on conversation branch
      - grants access via org-wide chat.moderate permission

    Permission errors
      - raises 403 when not club leader and lacks chat.moderate permission
      - club leader check does NOT apply to events
"""

from __future__ import annotations

from datetime import date, time, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Event, GroupChatMessage, SubscriptionPlan, OrganizationSubscription
from app.services.chat._helpers import (
    get_conversation_branch_id,
    get_message_or_404,
    require_chat_moderation_access,
    require_conversation_membership,
    validate_conversation,
)
from tests.conftest import make_org_with_owner


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
    create_club()'s plan-limit check. Applied uniformly here (even for
    tests that only create an event) so this file doesn't break again
    if create_event() ever grows its own plan-limit check."""
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


def _mock_current_user(user_id: str, permissions: list | None = None) -> MagicMock:
    """Create a mock CurrentUser for dependency injection."""
    mock = MagicMock()
    mock.user_id = user_id
    mock.permissions = permissions or []
    mock.is_super_admin = False
    return mock


# ── validate_conversation: happy path ───────────────────────────────────────

class TestValidateConversationHappyPath:

    @pytest.mark.asyncio
    async def test_returns_none_for_valid_club(self, db_session: AsyncSession):
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        result = await validate_conversation(
            session=db_session,
            organization_id=org.id,
            conversation_type="club",
            conversation_id=club.id,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_valid_event(self, db_session: AsyncSession):
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await validate_conversation(
            session=db_session,
            organization_id=org.id,
            conversation_type="event",
            conversation_id=event.id,
        )

        assert result is None


# ── validate_conversation: validation errors ─────────────────────────────────

class TestValidateConversationValidation:

    @pytest.mark.asyncio
    async def test_raises_400_for_invalid_conversation_type(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await validate_conversation(
                session=db_session,
                organization_id=org.id,
                conversation_type="invalid_type",
                conversation_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "conversation_type must be one of" in str(exc_info.value.detail)


# ── validate_conversation: not-found errors ─────────────────────────────────

class TestValidateConversationNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_club_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await validate_conversation(
                session=db_session,
                organization_id=org.id,
                conversation_type="club",
                conversation_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Club not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_club_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await _make_org_with_subscription(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch1 = await _make_branch(db_session, org1.id)
        owner1.branch_id = branch1.id
        await db_session.flush()
        club = await _make_club(db_session, org1.id, branch1.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await validate_conversation(
                session=db_session,
                organization_id=org2.id,
                conversation_type="club",
                conversation_id=club.id,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await validate_conversation(
                session=db_session,
                organization_id=org.id,
                conversation_type="event",
                conversation_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Event not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_event_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await _make_org_with_subscription(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch1 = await _make_branch(db_session, org1.id)
        owner1.branch_id = branch1.id
        await db_session.flush()
        event = await _make_event(db_session, org1.id, branch1.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await validate_conversation(
                session=db_session,
                organization_id=org2.id,
                conversation_type="event",
                conversation_id=event.id,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# ── require_conversation_membership: happy path ─────────────────────────────

class TestRequireConversationMembershipHappyPath:

    @pytest.mark.asyncio
    @patch("app.services.chat._helpers.require_club_membership")
    async def test_delegates_to_require_club_membership_for_club(
        self, mock_require_club, db_session: AsyncSession
    ):
        mock_require_club.return_value = AsyncMock()

        current_user = _mock_current_user("user-123")
        await require_conversation_membership(
            session=db_session,
            current_user=current_user,
            conversation_type="club",
            conversation_id="club-456",
        )

        mock_require_club.assert_called_once_with(
            db=db_session, current_user=current_user, club_id="club-456"
        )

    @pytest.mark.asyncio
    @patch("app.services.chat._helpers.require_event_participation")
    async def test_delegates_to_require_event_participation_for_event(
        self, mock_require_event, db_session: AsyncSession
    ):
        mock_require_event.return_value = AsyncMock()

        current_user = _mock_current_user("user-123")
        await require_conversation_membership(
            session=db_session,
            current_user=current_user,
            conversation_type="event",
            conversation_id="event-456",
        )

        mock_require_event.assert_called_once_with(
            db=db_session, current_user=current_user, event_id="event-456"
        )


# ── get_message_or_404: happy path ────────────────────────────────────────────

class TestGetMessageOr404HappyPath:

    @pytest.mark.asyncio
    async def test_returns_message_when_found(self, db_session: AsyncSession):
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(
            db_session, org.id, "club", club.id, owner.id, "Test message"
        )

        result = await get_message_or_404(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
        )

        assert isinstance(result, GroupChatMessage)
        assert result.id == message.id
        assert result.content == "Test message"


# ── get_message_or_404: not-found errors ────────────────────────────────────

class TestGetMessageOr404NotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_message_or_404(
                session=db_session,
                organization_id=org.id,
                message_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Message not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_message_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await _make_org_with_subscription(
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
        owner1.branch_id = branch1.id
        await db_session.flush()
        club = await _make_club(db_session, org1.id, branch1.id, owner1.id)
        message = await _make_message(db_session, org1.id, "club", club.id, owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await get_message_or_404(
                session=db_session,
                organization_id=org2.id,
                message_id=message.id,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# ── get_conversation_branch_id: happy path ────────────────────────────────────

class TestGetConversationBranchIdHappyPath:

    @pytest.mark.asyncio
    async def test_returns_branch_id_for_club(self, db_session: AsyncSession):
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id, name="Downtown Branch")
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        result = await get_conversation_branch_id(
            session=db_session,
            conversation_type="club",
            conversation_id=club.id,
        )

        assert result == branch.id

    @pytest.mark.asyncio
    async def test_returns_branch_id_for_event(self, db_session: AsyncSession):
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id, name="Uptown Branch")
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        result = await get_conversation_branch_id(
            session=db_session,
            conversation_type="event",
            conversation_id=event.id,
        )

        assert result == branch.id


# ── get_conversation_branch_id: not-found errors ──────────────────────────────

class TestGetConversationBranchIdNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_club_not_found(self, db_session: AsyncSession):
        with pytest.raises(HTTPException) as exc_info:
            await get_conversation_branch_id(
                session=db_session,
                conversation_type="club",
                conversation_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Club not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_404_when_event_not_found(self, db_session: AsyncSession):
        with pytest.raises(HTTPException) as exc_info:
            await get_conversation_branch_id(
                session=db_session,
                conversation_type="event",
                conversation_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Event not found" in str(exc_info.value.detail)


# ── require_chat_moderation_access: happy path ─────────────────────────────────

class TestRequireChatModerationAccessHappyPath:

    @pytest.mark.asyncio
    async def test_grants_access_to_club_leader(self, db_session: AsyncSession):
        """Club leader can moderate their own club's chat without any permission."""
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        # Should not raise
        result = await require_chat_moderation_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.chat._helpers.require_scoped_permission")
    async def test_grants_access_via_chat_moderate_permission(
        self, mock_require_permission, db_session: AsyncSession
    ):
        """Non-leader with chat.moderate permission can moderate."""
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Create another user who is NOT the leader
        from tests.conftest import make_user_with_role

        moderator = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="moderator",
            email="mod@example.com",
            branch_id=branch.id,
        )
        db_session.add(moderator)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(moderator.id)
        mock_require_permission.return_value = AsyncMock()

        await require_chat_moderation_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        # Verify require_scoped_permission was called with correct branch
        mock_require_permission.assert_called_once()
        call_kwargs = mock_require_permission.call_args.kwargs
        assert call_kwargs["permission"] == "chat.moderate"
        assert call_kwargs["target_branch_id"] == branch.id

    @pytest.mark.asyncio
    @patch("app.services.chat._helpers.require_scoped_permission")
    async def test_event_moderation_requires_permission_not_leader_check(
        self, mock_require_permission, db_session: AsyncSession
    ):
        """For events, there's no leader check — only permission matters."""
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "event", event.id, owner.id)

        # Even the event creator needs permission (no leader concept)
        current_user = _mock_current_user(owner.id)
        mock_require_permission.return_value = AsyncMock()

        await require_chat_moderation_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        # Should call require_scoped_permission since no leader check for events
        mock_require_permission.assert_called_once()


# ── require_chat_moderation_access: permission errors ─────────────────────────

class TestRequireChatModerationAccessPermissionErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat._helpers.require_scoped_permission")
    async def test_raises_403_when_not_leader_and_lacks_permission(
        self, mock_require_permission, db_session: AsyncSession
    ):
        """Non-leader without chat.moderate permission cannot moderate."""
        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        from tests.conftest import make_user_with_role

        regular_user = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="regular@example.com",
            branch_id=branch.id,
        )
        db_session.add(regular_user)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(regular_user.id)
        mock_require_permission.side_effect = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

        with pytest.raises(HTTPException) as exc_info:
            await require_chat_moderation_access(
                session=db_session,
                current_user=current_user,
                message=message,
            )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN