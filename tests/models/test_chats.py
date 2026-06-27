"""
GroupChatMessage / DirectMessage model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DirectMessage, GroupChatMessage, Club
from tests.conftest import make_user, make_org_with_owner, make_user_with_role


# ── Chat-specific factories ───────────────────────────────────────────────────

def make_group_chat_message(
    organization_id: str,
    conversation_type: str,
    conversation_id: str,
    user_id: str,
    **overrides,
) -> GroupChatMessage:
    defaults = dict(content="Hey everyone!")
    return GroupChatMessage(
        organization_id=organization_id,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        user_id=user_id,
        **{**defaults, **overrides},
    )


def make_direct_message(
    organization_id: str,
    sender_id: str,
    recipient_id: str,
    **overrides,
) -> DirectMessage:
    defaults = dict(content="Hey, got a minute?")
    return DirectMessage(
        organization_id=organization_id,
        sender_id=sender_id,
        recipient_id=recipient_id,
        **{**defaults, **overrides},
    )


async def setup_club(db_session: AsyncSession, org_id: str, **overrides) -> Club:
    defaults = dict(name="Running Club")
    club = Club(organization_id=org_id, **{**defaults, **overrides})
    db_session.add(club)
    await db_session.flush()
    return club


# ════════════════════════════════════════════════
# GroupChatMessage — basic persistence
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_group_chat_message_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()

    result = await db_session.execute(
        select(GroupChatMessage).where(GroupChatMessage.id == message.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id == org.id
    assert fetched.conversation_type == "club"
    assert fetched.conversation_id == club.id
    assert fetched.content == "Hey everyone!"
    assert fetched.is_pinned is False


@pytest.mark.asyncio
async def test_group_chat_message_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()

    parts = message.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_group_chat_message_organization_id_required(db_session: AsyncSession):
    """organization_id is NOT NULL — every group chat message is org-scoped."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = GroupChatMessage(
        organization_id=None,
        conversation_type="club",
        conversation_id=club.id,
        user_id=sender.id,
        content="Hey",
    )
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_can_be_media_only(db_session: AsyncSession):
    """content is nullable — a message can carry only media."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        content=None,
        media_url="https://cdn.example.com/photo.jpg",
        media_type="image",
    )
    db_session.add(message)
    await db_session.flush()

    assert message.content is None
    assert message.media_url == "https://cdn.example.com/photo.jpg"


@pytest.mark.asyncio
async def test_group_chat_message_requires_content_or_media(db_session: AsyncSession):
    """A message can't have both content and media_url null."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id, content=None)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_invalid_conversation_type_raises(db_session: AsyncSession):
    """conversation_type is constrained to a known set of values."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "challenge", club.id, sender.id)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_supports_event_conversation_type(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    fake_event_id = "11111111-1111-1111-1111-111111111111"
    message = make_group_chat_message(org.id, "event", fake_event_id, sender.id)
    db_session.add(message)
    await db_session.flush()

    assert message.conversation_type == "event"
    assert message.conversation_id == fake_event_id
    assert message.organization_id == org.id


# ════════════════════════════════════════════════
# GroupChatMessage — pinning
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_group_chat_message_can_be_pinned(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    manager = await make_user_with_role(db_session, organization_id=org.id, email="manager@example.com", role_name="branch_manager")
    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add_all([manager, sender])
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()

    message.is_pinned = True
    message.pinned_by = manager.id
    message.pinned_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert message.is_pinned is True
    assert message.pinned_by == manager.id
    assert message.pinned_at is not None


@pytest.mark.asyncio
async def test_group_chat_message_only_one_pinned_per_conversation(db_session: AsyncSession):
    """Pinning a second message in the same conversation must fail —
    enforced by a partial unique index, not application logic."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    manager = await make_user_with_role(db_session, organization_id=org.id, email="manager@example.com", role_name="branch_manager")
    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add_all([manager, sender])
    await db_session.flush()

    pinned_at = datetime.now(timezone.utc)

    first = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        content="First", is_pinned=True, pinned_by=manager.id, pinned_at=pinned_at,
    )
    db_session.add(first)
    await db_session.flush()

    second = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        content="Second", is_pinned=True, pinned_by=manager.id, pinned_at=pinned_at,
    )
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_different_conversations_can_each_have_a_pin(db_session: AsyncSession):
    """The unique constraint is scoped per conversation — two different
    clubs can each have their own pinned message."""
    org, _ = await make_org_with_owner(db_session)
    club_a = await setup_club(db_session, org.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, name="Book Club")

    manager = await make_user_with_role(db_session, organization_id=org.id, email="manager@example.com", role_name="branch_manager")
    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add_all([manager, sender])
    await db_session.flush()

    pinned_at = datetime.now(timezone.utc)

    db_session.add(
        make_group_chat_message(
            org.id, "club", club_a.id, sender.id,
            is_pinned=True, pinned_by=manager.id, pinned_at=pinned_at,
        )
    )
    db_session.add(
        make_group_chat_message(
            org.id, "club", club_b.id, sender.id,
            is_pinned=True, pinned_by=manager.id, pinned_at=pinned_at,
        )
    )
    await db_session.flush()

    result = await db_session.execute(
        select(GroupChatMessage).where(GroupChatMessage.is_pinned == True)  # noqa: E712
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_group_chat_message_can_be_unpinned_then_repinned(db_session: AsyncSession):
    """Unpinning frees up the slot — another message can then be pinned."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    manager = await make_user_with_role(db_session, organization_id=org.id, email="manager@example.com", role_name="branch_manager")
    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add_all([manager, sender])
    await db_session.flush()

    pinned_at = datetime.now(timezone.utc)

    first = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        content="First", is_pinned=True, pinned_by=manager.id, pinned_at=pinned_at,
    )
    db_session.add(first)
    await db_session.flush()

    first.is_pinned = False
    first.pinned_by = None
    first.pinned_at = None
    await db_session.flush()

    second = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        content="Second", is_pinned=True, pinned_by=manager.id, pinned_at=pinned_at,
    )
    db_session.add(second)
    await db_session.flush()

    assert second.is_pinned is True


@pytest.mark.asyncio
async def test_group_chat_message_pin_without_pinned_by_raises(db_session: AsyncSession):
    """is_pinned=true with null audit fields is rejected by the pin
    consistency constraint."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        is_pinned=True,
        pinned_by=None,
        pinned_at=None,
    )
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_ghost_pin_fields_without_is_pinned_raises(db_session: AsyncSession):
    """pinned_by set while is_pinned=false is ghost state — rejected by the
    pin consistency constraint."""
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    manager = await make_user_with_role(db_session, organization_id=org.id, email="manager@example.com", role_name="branch_manager")
    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add_all([manager, sender])
    await db_session.flush()

    message = make_group_chat_message(
        org.id, "club", club.id, sender.id,
        is_pinned=False,
        pinned_by=manager.id,
        pinned_at=datetime.now(timezone.utc),
    )
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ════════════════════════════════════════════════
# GroupChatMessage — relationships & cascade
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_group_chat_message_organization_relationship(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()
    await db_session.refresh(message, ["organization"])

    assert message.organization is not None
    assert message.organization.id == org.id


@pytest.mark.asyncio
async def test_organization_delete_cascades_to_group_chat_messages(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(organization_id=org.id, email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()
    message_id = message.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(GroupChatMessage).where(GroupChatMessage.id == message_id)
    )
    assert result.scalar_one_or_none() is None


# ════════════════════════════════════════════════
# DirectMessage — basic persistence
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_direct_message_persists(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id)
    db_session.add(message)
    await db_session.flush()

    result = await db_session.execute(select(DirectMessage).where(DirectMessage.id == message.id))
    fetched = result.scalar_one()
    assert fetched.sender_id == sender.id
    assert fetched.recipient_id == recipient.id
    assert fetched.content == "Hey, got a minute?"
    assert fetched.is_read is False


@pytest.mark.asyncio
async def test_direct_message_id_is_uuid_format(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id)
    db_session.add(message)
    await db_session.flush()

    parts = message.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_direct_message_can_be_media_only(db_session: AsyncSession):
    """content is nullable — a DM can carry only media."""
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(
        org.id, sender.id, recipient.id,
        content=None,
        media_url="https://cdn.example.com/clip.mp4",
        media_type="video",
    )
    db_session.add(message)
    await db_session.flush()

    assert message.content is None
    assert message.media_url == "https://cdn.example.com/clip.mp4"


@pytest.mark.asyncio
async def test_direct_message_requires_content_or_media(db_session: AsyncSession):
    """A DM can't have both content and media_url null."""
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id, content=None)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_direct_message_has_no_pinning_fields(db_session: AsyncSession):
    """Pinning only applies to group chats, not direct messages."""
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id)
    assert not hasattr(message, "is_pinned")
    assert not hasattr(message, "pinned_by")
    assert not hasattr(message, "pinned_at")


@pytest.mark.asyncio
async def test_direct_message_can_be_marked_read(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id)
    db_session.add(message)
    await db_session.flush()

    message.is_read = True
    message.read_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert message.is_read is True
    assert message.read_at is not None


@pytest.mark.asyncio
async def test_direct_message_thread_between_two_users(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session)

    sender = make_user(organization_id=org.id, email="dm_sender@example.com")
    recipient = make_user(organization_id=org.id, email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    db_session.add(make_direct_message(org.id, sender.id, recipient.id, content="Hi"))
    db_session.add(make_direct_message(org.id, recipient.id, sender.id, content="Hey back"))
    await db_session.flush()

    result = await db_session.execute(
        select(DirectMessage).where(
            DirectMessage.sender_id.in_([sender.id, recipient.id])
        )
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_direct_message_self_message_raises(db_session: AsyncSession):
    """sender_id == recipient_id is rejected at the DB level."""
    org, _ = await make_org_with_owner(db_session)

    user = make_user(organization_id=org.id, email="solo@example.com")
    db_session.add(user)
    await db_session.flush()

    message = make_direct_message(org.id, user.id, user.id)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()