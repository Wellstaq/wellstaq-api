"""
tests/services/chat/test_pin_service.py

Unit tests for app/services/chat/pin_service.py

Coverage:
  pin_message()
    Happy path
      - pins message when user has moderation access
      - returns updated GroupChatMessage with is_pinned=True
      - sets pinned_by and pinned_at fields
      - unpins existing pinned message in same conversation (swap)
      - only affects messages in the same conversation

    Not-found errors
      - raises 404 when message not found
      - raises 404 when message belongs to different organization

    Permission errors
      - raises 403 when user lacks moderation access

  unpin_message()
    Happy path
      - unpins message when user has moderation access
      - returns updated GroupChatMessage with is_pinned=False
      - clears pinned_by and pinned_at fields

    Not-found errors
      - raises 404 when message not found

    Permission errors
      - raises 403 when user lacks moderation access

    Idempotency
      - unpinning already-unpinned message succeeds without error
"""

from __future__ import annotations

from datetime import date, time, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from app.models import Club, Event, GroupChatMessage, SubscriptionPlan, OrganizationSubscription
from app.services.chat.pin_service import pin_message, unpin_message
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


async def _make_message(
    db_session: AsyncSession,
    organization_id: str,
    conversation_type: str,
    conversation_id: str,
    sender_id: str,
    content: str = "Hello world",
    is_pinned: bool = False,
) -> GroupChatMessage:
    message = GroupChatMessage(
        organization_id=organization_id,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        user_id=sender_id,
        content=content,
        is_pinned=is_pinned,
    )
    if is_pinned:
        message.pinned_by = sender_id
        message.pinned_at = datetime.now(timezone.utc)
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


# ── pin_message: happy path ─────────────────────────────────────────────────

class TestPinMessageHappyPath:

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_pins_message_with_moderation_access(self, mock_require_mod, db_session: AsyncSession):
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await pin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert result.is_pinned is True
        assert result.pinned_by == owner.id
        assert result.pinned_at is not None

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_returns_group_chat_message_instance(self, mock_require_mod, db_session: AsyncSession):
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await pin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert isinstance(result, GroupChatMessage)

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_unpins_existing_pinned_message_in_same_conversation(
        self, mock_require_mod, db_session: AsyncSession
    ):
        """Pinning a new message must unpin the old one (DB unique constraint)."""
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Create already-pinned message
        old_message = await _make_message(
            db_session, org.id, "club", club.id, owner.id, "Old pinned", is_pinned=True
        )
        old_message_id = old_message.id

        # Create new message to pin
        new_message = await _make_message(db_session, org.id, "club", club.id, owner.id, "New pinned")

        current_user = _mock_current_user(owner.id)

        result = await pin_message(
            session=db_session,
            organization_id=org.id,
            message_id=new_message.id,
            current_user=current_user,
        )

        # New message is pinned
        assert result.is_pinned is True
        assert result.id == new_message.id

        # Old message is unpinned in DB
        await db_session.refresh(old_message)
        assert old_message.is_pinned is False
        assert old_message.pinned_by is None
        assert old_message.pinned_at is None

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_only_unpins_messages_in_same_conversation(
        self, mock_require_mod, db_session: AsyncSession
    ):
        """Pinning in club A should not unpin in club B."""
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club_a = await _make_club(db_session, org.id, branch.id, owner.id, name="Club A")
        club_b = await _make_club(db_session, org.id, branch.id, owner.id, name="Club B")

        # Pinned message in club B
        message_b = await _make_message(
            db_session, org.id, "club", club_b.id, owner.id, "In B", is_pinned=True
        )

        # New message in club A
        message_a = await _make_message(db_session, org.id, "club", club_a.id, owner.id, "In A")

        current_user = _mock_current_user(owner.id)

        await pin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message_a.id,
            current_user=current_user,
        )

        # Message in club B should still be pinned
        await db_session.refresh(message_b)
        assert message_b.is_pinned is True

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_pinned_at_timestamp_is_utc(self, mock_require_mod, db_session: AsyncSession):
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        before = datetime.now(timezone.utc)
        result = await pin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )
        after = datetime.now(timezone.utc)
       
        assert before <= result.pinned_at <= after
        assert result.pinned_at.tzinfo == timezone.utc


# ── pin_message: not-found errors ───────────────────────────────────────────

class TestPinMessageNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        current_user = _mock_current_user("user-123")

        with pytest.raises(HTTPException) as exc_info:
            await pin_message(
                session=db_session,
                organization_id=org.id,
                message_id="00000000-0000-0000-0000-000000000000",
                current_user=current_user,
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

        current_user = _mock_current_user(owner2.id)

        with pytest.raises(HTTPException) as exc_info:
            await pin_message(
                session=db_session,
                organization_id=org2.id,
                message_id=message.id,
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# ── pin_message: permission errors ─────────────────────────────────────────

class TestPinMessagePermissionErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_raises_403_when_lacks_moderation_access(
        self, mock_require_mod, db_session: AsyncSession
    ):
        mock_require_mod.side_effect = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user("unauthorized-user")

        with pytest.raises(HTTPException) as exc_info:
            await pin_message(
                session=db_session,
                organization_id=org.id,
                message_id=message.id,
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


# ── unpin_message: happy path ────────────────────────────────────────────────

class TestUnpinMessageHappyPath:

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_unpins_message_with_moderation_access(self, mock_require_mod, db_session: AsyncSession):
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(
            db_session, org.id, "club", club.id, owner.id, is_pinned=True
        )

        current_user = _mock_current_user(owner.id)

        result = await unpin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert result.is_pinned is False
        assert result.pinned_by is None
        assert result.pinned_at is None

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_returns_group_chat_message_instance(self, mock_require_mod, db_session: AsyncSession):
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(
            db_session, org.id, "club", club.id, owner.id, is_pinned=True
        )

        current_user = _mock_current_user(owner.id)

        result = await unpin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert isinstance(result, GroupChatMessage)


# ── unpin_message: not-found errors ─────────────────────────────────────────

class TestUnpinMessageNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        current_user = _mock_current_user("user-123")

        with pytest.raises(HTTPException) as exc_info:
            await unpin_message(
                session=db_session,
                organization_id=org.id,
                message_id="00000000-0000-0000-0000-000000000000",
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Message not found" in str(exc_info.value.detail)


# ── unpin_message: permission errors ────────────────────────────────────────

class TestUnpinMessagePermissionErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_raises_403_when_lacks_moderation_access(
        self, mock_require_mod, db_session: AsyncSession
    ):
        mock_require_mod.side_effect = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(
            db_session, org.id, "club", club.id, owner.id, is_pinned=True
        )

        current_user = _mock_current_user("unauthorized-user")

        with pytest.raises(HTTPException) as exc_info:
            await unpin_message(
                session=db_session,
                organization_id=org.id,
                message_id=message.id,
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


# ── unpin_message: idempotency ─────────────────────────────────────────────

class TestUnpinMessageIdempotency:

    @pytest.mark.asyncio
    @patch("app.services.chat.pin_service.require_chat_moderation_access")
    async def test_unpinning_already_unpinned_succeeds(self, mock_require_mod, db_session: AsyncSession):
        """Unpinning a message that is not pinned should not error."""
        mock_require_mod.return_value = AsyncMock()

        org, owner = await _make_org_with_subscription(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(
            db_session, org.id, "club", club.id, owner.id, is_pinned=False
        )

        current_user = _mock_current_user(owner.id)

        # Should not raise
        result = await unpin_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert result.is_pinned is False
        assert result.pinned_by is None
        assert result.pinned_at is None