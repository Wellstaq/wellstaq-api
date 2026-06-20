"""
GroupChatMessage / DirectMessage model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DirectMessage, GroupChatMessage, Club, Organization, User


# Factories

def make_user(email: str = "user@example.com", **overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        role_name="employee",
        hashed_password="$2b$12$fakehash",
    )
    return User(**{**defaults, **overrides})


def make_org(owner_id: str, **overrides) -> Organization:
    defaults = dict(
        business_name="Acme Corp",
        slug="acme-corp",
        owner_id=owner_id,
    )
    return Organization(**{**defaults, **overrides})


def make_club(organization_id: str, **overrides) -> Club:
    defaults = dict(name="Running Club", organization_id=organization_id)
    return Club(**{**defaults, **overrides})


def make_group_chat_message(conversation_type: str, conversation_id: str, user_id: str, **overrides) -> GroupChatMessage:
    defaults = dict(content="Hey everyone!")
    return GroupChatMessage(
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        user_id=user_id,
        **{**defaults, **overrides},
    )


def make_direct_message(organization_id: str, sender_id: str, recipient_id: str, **overrides) -> DirectMessage:
    defaults = dict(content="Hey, got a minute?")
    return DirectMessage(
        organization_id=organization_id,
        sender_id=sender_id,
        recipient_id=recipient_id,
        **{**defaults, **overrides},
    )


# Helpers

async def setup_org(
    db_session: AsyncSession,
    email: str = "owner@example.com",
    slug: str = "acme-corp",
):
    user = make_user(email=email, role_name="overall_manager")
    db_session.add(user)
    await db_session.flush()

    org = make_org(user.id, slug=slug)
    db_session.add(org)
    await db_session.flush()

    return user, org


async def setup_club(db_session: AsyncSession, org_id: str, **overrides) -> Club:
    club = make_club(org_id, **overrides)
    db_session.add(club)
    await db_session.flush()
    return club


# ════════════════════════════════════════════════
# GroupChatMessage model
# ════════════════════════════════════════════════

# Basic persistence

@pytest.mark.asyncio
async def test_group_chat_message_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message("club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()

    result = await db_session.execute(
        select(GroupChatMessage).where(GroupChatMessage.id == message.id)
    )
    fetched = result.scalar_one()
    assert fetched.conversation_type == "club"
    assert fetched.conversation_id == club.id
    assert fetched.content == "Hey everyone!"
    assert fetched.is_pinned is False


@pytest.mark.asyncio
async def test_group_chat_message_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message("club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()

    parts = message.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_group_chat_message_can_be_media_only(db_session: AsyncSession):
    """content is nullable — a message can carry only media."""
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(
        "club", club.id, sender.id,
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
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message("club", club.id, sender.id, content=None)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_invalid_conversation_type_raises(db_session: AsyncSession):
    """conversation_type is constrained to a known set of values."""
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message("challenge", club.id, sender.id)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_supports_event_conversation_type(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    # event_id is just a UUID-shaped string here — no events table FK to
    # satisfy at the model layer, since conversation_id isn't a real FK.
    fake_event_id = "11111111-1111-1111-1111-111111111111"
    message = make_group_chat_message("event", fake_event_id, sender.id)
    db_session.add(message)
    await db_session.flush()

    assert message.conversation_type == "event"
    assert message.conversation_id == fake_event_id


# Pinning

@pytest.mark.asyncio
async def test_group_chat_message_can_be_pinned(db_session: AsyncSession):
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message("club", club.id, sender.id)
    db_session.add(message)
    await db_session.flush()

    message.is_pinned = True
    message.pinned_by = manager.id
    await db_session.flush()

    assert message.is_pinned is True
    assert message.pinned_by == manager.id


@pytest.mark.asyncio
async def test_group_chat_message_only_one_pinned_per_conversation(db_session: AsyncSession):
    """Pinning a second message in the same conversation must fail —
    enforced by a partial unique index, not application logic."""
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    first = make_group_chat_message("club", club.id, sender.id, content="First", is_pinned=True, pinned_by=manager.id)
    db_session.add(first)
    await db_session.flush()

    second = make_group_chat_message("club", club.id, sender.id, content="Second", is_pinned=True, pinned_by=manager.id)
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_different_conversations_can_each_have_a_pin(db_session: AsyncSession):
    """The unique constraint is scoped per conversation — two different
    clubs can each have their own pinned message."""
    _, org = await setup_org(db_session)
    club_a = await setup_club(db_session, org.id, name="Running Club")
    club_b = await setup_club(db_session, org.id, name="Book Club")

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    db_session.add(
        make_group_chat_message("club", club_a.id, sender.id, is_pinned=True, pinned_by=manager.id)
    )
    db_session.add(
        make_group_chat_message("club", club_b.id, sender.id, is_pinned=True, pinned_by=manager.id)
    )
    await db_session.flush()

    result = await db_session.execute(
        select(GroupChatMessage).where(GroupChatMessage.is_pinned == True)  # noqa: E712
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_group_chat_message_can_be_unpinned_then_repinned(db_session: AsyncSession):
    """Unpinning frees up the slot — another message can then be pinned."""
    _, org = await setup_org(db_session)
    club = await setup_club(db_session, org.id)

    manager = make_user(email="manager@example.com", role_name="branch_manager")
    db_session.add(manager)
    await db_session.flush()

    sender = make_user(email="sender@example.com")
    db_session.add(sender)
    await db_session.flush()

    first = make_group_chat_message("club", club.id, sender.id, content="First", is_pinned=True, pinned_by=manager.id)
    db_session.add(first)
    await db_session.flush()

    # Unpin first
    first.is_pinned = False
    first.pinned_by = None
    await db_session.flush()

    second = make_group_chat_message("club", club.id, sender.id, content="Second", is_pinned=True, pinned_by=manager.id)
    db_session.add(second)
    await db_session.flush()

    assert second.is_pinned is True


# ════════════════════════════════════════════════
# DirectMessage model
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_direct_message_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
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
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
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
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
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
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id, content=None)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_direct_message_has_no_pinning_fields(db_session: AsyncSession):
    """Pinning only applies to group chats, not direct messages."""
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id)
    assert not hasattr(message, "is_pinned")
    assert not hasattr(message, "pinned_by")
    assert not hasattr(message, "pinned_at")


@pytest.mark.asyncio
async def test_direct_message_can_be_marked_read(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
    db_session.add_all([sender, recipient])
    await db_session.flush()

    message = make_direct_message(org.id, sender.id, recipient.id)
    db_session.add(message)
    await db_session.flush()

    from datetime import datetime, timezone
    message.is_read = True
    message.read_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert message.is_read is True
    assert message.read_at is not None


@pytest.mark.asyncio
async def test_direct_message_thread_between_two_users(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    sender = make_user(email="dm_sender@example.com")
    recipient = make_user(email="dm_recipient@example.com")
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