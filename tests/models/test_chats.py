
"""
GroupChatMessage model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Key fix vs prior version:
  User.branch_id is NOT NULL — every make_user() call now passes
  branch_id=owner.branch_id (the HQ branch auto-created by
  make_org_with_owner). make_user_with_role() calls pass it too.
  setup_org() now returns (org, owner) so branch_id is available
  at every call site without a separate query.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupChatMessage, Club
from app.models.branch import Branch
from tests.conftest import make_user, make_org_with_owner, make_user_with_role


# ── Factories ─────────────────────────────────────────────────────────────────

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


async def setup_club(
    db_session: AsyncSession,
    org_id: str,
    branch_id: str,
    *,
    name: str = "Running Club",
    **overrides,
) -> Club:
    """branch_id is required — Club.branch_id is NOT NULL."""
    defaults = dict(privacy="public")
    club = Club(
        organization_id=org_id,
        branch_id=branch_id,
        name=name,
        **{**defaults, **overrides},
    )
    db_session.add(club)
    await db_session.flush()
    return club


async def setup_org(db_session: AsyncSession, *, slug: str = "acme-corp"):
    """Returns (org, owner) — owner.branch_id is the HQ branch."""
    org, owner = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=slug),
        user_kwargs=dict(email="owner@example.com", role_name="overall_manager"),
    )
    return org, owner


# ── Basic persistence ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_chat_message_persists(db_session: AsyncSession):
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "club", club.id, sender.id, content=None)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_invalid_conversation_type_raises(db_session: AsyncSession):
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
    db_session.add(sender)
    await db_session.flush()

    message = make_group_chat_message(org.id, "challenge", club.id, sender.id)
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_group_chat_message_supports_event_conversation_type(db_session: AsyncSession):
    org, owner = await setup_org(db_session)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
    db_session.add(sender)
    await db_session.flush()

    fake_event_id = "11111111-1111-1111-1111-111111111111"
    message = make_group_chat_message(org.id, "event", fake_event_id, sender.id)
    db_session.add(message)
    await db_session.flush()

    assert message.conversation_type == "event"
    assert message.conversation_id == fake_event_id
    assert message.organization_id == org.id


# ── Pinning ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_chat_message_can_be_pinned(db_session: AsyncSession):
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    manager = await make_user_with_role(
        db_session, organization_id=org.id, email="manager@example.com",
        role_name="branch_manager", branch_id=owner.branch_id,
    )
    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    manager = await make_user_with_role(
        db_session, organization_id=org.id, email="manager@example.com",
        role_name="branch_manager", branch_id=owner.branch_id,
    )
    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
async def test_group_chat_message_different_conversations_can_each_have_a_pin(
    db_session: AsyncSession,
):
    org, owner = await setup_org(db_session)

    # Two clubs on the same branch — each gets its own pin slot
    club_a = await setup_club(db_session, org.id, owner.branch_id, name="Running Club")
    club_b = await setup_club(db_session, org.id, owner.branch_id, name="Book Club")

    manager = await make_user_with_role(
        db_session, organization_id=org.id, email="manager@example.com",
        role_name="branch_manager", branch_id=owner.branch_id,
    )
    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    """Unpinning frees the slot — a different message can then be pinned."""
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    manager = await make_user_with_role(
        db_session, organization_id=org.id, email="manager@example.com",
        role_name="branch_manager", branch_id=owner.branch_id,
    )
    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    """is_pinned=true with null audit fields is rejected by the
    pin consistency constraint."""
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
async def test_group_chat_message_ghost_pin_fields_without_is_pinned_raises(
    db_session: AsyncSession,
):
    """pinned_by set while is_pinned=false is ghost state — rejected by
    the pin consistency constraint."""
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    manager = await make_user_with_role(
        db_session, organization_id=org.id, email="manager@example.com",
        role_name="branch_manager", branch_id=owner.branch_id,
    )
    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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


# ── Relationships & cascade ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_chat_message_organization_relationship(db_session: AsyncSession):
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
    org, owner = await setup_org(db_session)
    club = await setup_club(db_session, org.id, owner.branch_id)

    sender = make_user(organization_id=org.id, email="sender@example.com", branch_id=owner.branch_id)
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
