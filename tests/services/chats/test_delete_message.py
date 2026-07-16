"""
tests/services/chat/test_delete_message.py

Unit tests for app/services/chat/delete_message.py

Coverage:
  delete_message()
    Happy path
      - deletes message when user is club leader
      - deletes message when user is event creator
      - deletes message when user has chat.moderate permission
      - returns None after successful deletion
      - logs deletion action with correct metadata

    Permission errors
      - raises 403 when user is not leader, not creator, and lacks permission
      - club leader check does NOT apply to events
      - event creator check does NOT apply to clubs

    Not-found errors
      - raises 404 when message not found
      - raises 404 when message belongs to different organization

  _has_deletion_access()
    Happy path
      - returns True for club leader
      - returns True for event creator
      - returns False for regular member

    Edge cases
      - returns False when club not found
      - returns False when event not found
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Event, GroupChatMessage
from app.services.chat.delete_message import _has_deletion_access, delete_message
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


def _mock_current_user(user_id: str, permissions: list | None = None) -> MagicMock:
    """Create a mock CurrentUser for dependency injection."""
    mock = MagicMock()
    mock.user_id = user_id
    mock.permissions = permissions or []
    mock.is_super_admin = False
    return mock


# ── delete_message: happy path (club leader) ───────────────────────────────────

class TestDeleteMessageClubLeader:

    @pytest.mark.asyncio
    async def test_club_leader_can_delete_any_message_in_club(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Another user sends a message
        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="member@example.com",
            branch_id=branch.id,
        )
        db_session.add(member)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "club", club.id, member.id)

        # Club leader deletes it
        current_user = _mock_current_user(owner.id)

        result = await delete_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert result is None

        # Verify message is gone
        verify = await db_session.execute(
            select(GroupChatMessage).where(GroupChatMessage.id == message.id)
        )
        assert verify.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_club_leader_deletion_does_not_require_permission(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)  # No permissions

        with patch("app.services.chat.delete_message.require_scoped_permission") as mock_perm:
            await delete_message(
                session=db_session,
                organization_id=org.id,
                message_id=message.id,
                current_user=current_user,
            )
            # Should NOT call require_scoped_permission
            mock_perm.assert_not_called()


# ── delete_message: happy path (event creator) ───────────────────────────────

class TestDeleteMessageEventCreator:

    @pytest.mark.asyncio
    async def test_event_creator_can_delete_any_message_in_event(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Another user sends a message
        participant = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="participant@example.com",
            branch_id=branch.id,
        )
        db_session.add(participant)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "event", event.id, participant.id)

        # Event creator deletes it
        current_user = _mock_current_user(owner.id)

        result = await delete_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert result is None

        # Verify message is gone
        verify = await db_session.execute(
            select(GroupChatMessage).where(GroupChatMessage.id == message.id)
        )
        assert verify.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_event_creator_deletion_does_not_require_permission(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "event", event.id, owner.id)

        current_user = _mock_current_user(owner.id)  # No permissions

        with patch("app.services.chat.delete_message.require_scoped_permission") as mock_perm:
            await delete_message(
                session=db_session,
                organization_id=org.id,
                message_id=message.id,
                current_user=current_user,
            )
            # Should NOT call require_scoped_permission
            mock_perm.assert_not_called()


# ── delete_message: happy path (chat.moderate permission) ─────────────────────

class TestDeleteMessageWithModeratePermission:

    @pytest.mark.asyncio
    @patch("app.services.chat.delete_message.require_scoped_permission")
    async def test_user_with_chat_moderate_can_delete(self, mock_require_permission, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Non-leader with permission
        moderator = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="moderator",
            email="mod@example.com",
            branch_id=branch.id,
        )
        db_session.add(moderator)
        await db_session.flush()

        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="member@example.com",
            branch_id=branch.id,
        )
        db_session.add(member)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "club", club.id, member.id)

        current_user = _mock_current_user(moderator.id)
        mock_require_permission.return_value = AsyncMock()

        result = await delete_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        assert result is None

        # Verify require_scoped_permission was called
        mock_require_permission.assert_called_once()
        call_kwargs = mock_require_permission.call_args.kwargs
        assert call_kwargs["permission"] == "chat.moderate"
        assert call_kwargs["target_branch_id"] == branch.id

    @pytest.mark.asyncio
    @patch("app.services.chat.delete_message.require_scoped_permission")
    async def test_permission_check_for_event_conversation(self, mock_require_permission, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # Non-creator with permission
        moderator = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="moderator",
            email="mod@example.com",
            branch_id=branch.id,
        )
        db_session.add(moderator)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "event", event.id, owner.id)

        current_user = _mock_current_user(moderator.id)
        mock_require_permission.return_value = AsyncMock()

        await delete_message(
            session=db_session,
            organization_id=org.id,
            message_id=message.id,
            current_user=current_user,
        )

        # Should check permission for event's branch
        mock_require_permission.assert_called_once()
        call_kwargs = mock_require_permission.call_args.kwargs
        assert call_kwargs["target_branch_id"] == branch.id


# ── delete_message: logging ───────────────────────────────────────────────────

class TestDeleteMessageLogging:

    @pytest.mark.asyncio
    async def test_logs_deletion_with_correct_metadata(self, db_session: AsyncSession, caplog):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        with caplog.at_level("INFO"):
            await delete_message(
                session=db_session,
                organization_id=org.id,
                message_id=message.id,
                current_user=current_user,
            )

        assert "Group chat message deleted" in caplog.text
        assert str(message.id) in caplog.text
        assert str(owner.id) in caplog.text
        assert "club" in caplog.text
        assert str(club.id) in caplog.text


# ── delete_message: permission errors ─────────────────────────────────────────

class TestDeleteMessagePermissionErrors:

    @pytest.mark.asyncio
    @patch("app.services.chat.delete_message.require_scoped_permission")
    async def test_raises_403_when_no_access_paths_grant_permission(
        self, mock_require_permission, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # Regular member, not leader, no permission
        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="member@example.com",
            branch_id=branch.id,
        )
        db_session.add(member)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(member.id)
        mock_require_permission.side_effect = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_message(
                session=db_session,
                organization_id=org.id,
                message_id=message.id,
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_club_leader_check_does_not_apply_to_events(self, db_session: AsyncSession):
        """Being a club leader does NOT grant deletion rights in events."""
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        # Owner is club leader
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        # But event is created by someone else
        event_creator = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="creator@example.com",
            branch_id=branch.id,
        )
        db_session.add(event_creator)
        await db_session.flush()

        event = await _make_event(db_session, org.id, branch.id, event_creator.id)
        message = await _make_message(db_session, org.id, "event", event.id, event_creator.id)

        # Club leader tries to delete event message — should fail
        current_user = _mock_current_user(owner.id)

        with patch("app.services.chat.delete_message.require_scoped_permission") as mock_perm:
            mock_perm.side_effect = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
            )

            with pytest.raises(HTTPException) as exc_info:
                await delete_message(
                    session=db_session,
                    organization_id=org.id,
                    message_id=message.id,
                    current_user=current_user,
                )

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_event_creator_check_does_not_apply_to_clubs(self, db_session: AsyncSession):
        """Being an event creator does NOT grant deletion rights in clubs."""
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        # Owner created an event
        event = await _make_event(db_session, org.id, branch.id, owner.id)

        # But club is led by someone else
        club_leader = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="leader@example.com",
            branch_id=branch.id,
        )
        db_session.add(club_leader)
        await db_session.flush()

        club = await _make_club(db_session, org.id, branch.id, club_leader.id)
        message = await _make_message(db_session, org.id, "club", club.id, club_leader.id)

        # Event creator tries to delete club message — should fail
        current_user = _mock_current_user(owner.id)

        with patch("app.services.chat.delete_message.require_scoped_permission") as mock_perm:
            mock_perm.side_effect = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
            )

            with pytest.raises(HTTPException) as exc_info:
                await delete_message(
                    session=db_session,
                    organization_id=org.id,
                    message_id=message.id,
                    current_user=current_user,
                )

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


# ── delete_message: not-found errors ──────────────────────────────────────────

class TestDeleteMessageNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        current_user = _mock_current_user("user-123")

        with pytest.raises(HTTPException) as exc_info:
            await delete_message(
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
        owner1.branch_id = branch1.id
        await db_session.flush()
        club = await _make_club(db_session, org1.id, branch1.id, owner1.id)
        message = await _make_message(db_session, org1.id, "club", club.id, owner1.id)

        current_user = _mock_current_user(owner2.id)

        with pytest.raises(HTTPException) as exc_info:
            await delete_message(
                session=db_session,
                organization_id=org2.id,
                message_id=message.id,
                current_user=current_user,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# ── _has_deletion_access: happy path ──────────────────────────────────────────

class TestHasDeletionAccessHappyPath:

    @pytest.mark.asyncio
    async def test_returns_true_for_club_leader(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await _has_deletion_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_for_event_creator(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        event = await _make_event(db_session, org.id, branch.id, owner.id)
        message = await _make_message(db_session, org.id, "event", event.id, owner.id)

        current_user = _mock_current_user(owner.id)

        result = await _has_deletion_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_for_regular_member(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()
        club = await _make_club(db_session, org.id, branch.id, owner.id)

        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            role_name="member",
            email="member@example.com",
            branch_id=branch.id,
        )
        db_session.add(member)
        await db_session.flush()

        message = await _make_message(db_session, org.id, "club", club.id, owner.id)

        current_user = _mock_current_user(member.id)

        result = await _has_deletion_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        assert result is False


# ── _has_deletion_access: edge cases ──────────────────────────────────────────

class TestHasDeletionAccessEdgeCases:

    @pytest.mark.asyncio
    async def test_returns_false_when_club_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        # Create message with non-existent club
        message = GroupChatMessage(
            organization_id=org.id,
            conversation_type="club",
            conversation_id="00000000-0000-0000-0000-000000000000",
            user_id=owner.id,
            content="Test",
        )
        db_session.add(message)
        await db_session.flush()
        await db_session.refresh(message)

        current_user = _mock_current_user(owner.id)

        result = await _has_deletion_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_event_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        # Create message with non-existent event
        message = GroupChatMessage(
            organization_id=org.id,
            conversation_type="event",
            conversation_id="00000000-0000-0000-0000-000000000000",
            user_id =owner.id,
            content="Test",
        )
        db_session.add(message)
        await db_session.flush()
        await db_session.refresh(message)

        current_user = _mock_current_user(owner.id)

        result = await _has_deletion_access(
            session=db_session,
            current_user=current_user,
            message=message,
        )

        assert result is False