"""
Notification model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from app.models import Organization
from app.models  import User


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


def make_notification(user_id: str, organization_id: str, **overrides) -> Notification:
    defaults = dict(
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id="11111111-1111-1111-1111-111111111111",
        reference_type="post",
    )
    return Notification(
        user_id=user_id,
        organization_id=organization_id,
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


# Notification model — basic persistence

@pytest.mark.asyncio
async def test_notification_persists(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    actor = make_user(email="actor@example.com")
    db_session.add_all([recipient, actor])
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, actor_id=actor.id)
    db_session.add(notification)
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(Notification.id == notification.id)
    )
    fetched = result.scalar_one()
    assert fetched.user_id == recipient.id
    assert fetched.actor_id == actor.id
    assert fetched.notification_type == "liked_post"
    assert fetched.body == "Ada liked your post"
    assert fetched.is_read is False


@pytest.mark.asyncio
async def test_notification_id_is_uuid_format(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    parts = notification.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_notification_actor_can_be_null_for_system_generated(db_session: AsyncSession):
    """System-generated notifications (e.g. event reminders) have no
    triggering user."""
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(
        recipient.id, org.id,
        notification_type="event_reminder",
        body="Your event starts in 1 hour",
        reference_id="22222222-2222-2222-2222-222222222222",
        reference_type="event",
        actor_id=None,
    )
    db_session.add(notification)
    await db_session.flush()

    assert notification.actor_id is None


@pytest.mark.asyncio
async def test_notification_can_have_no_reference(db_session: AsyncSession):
    """A broadcast announcement has no underlying record — reference_id
    and reference_type are both null together."""
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="announcement",
        body="App maintenance tonight at 10pm",
        reference_id=None,
        reference_type=None,
    )
    db_session.add(notification)
    await db_session.flush()

    assert notification.reference_id is None
    assert notification.reference_type is None


@pytest.mark.asyncio
async def test_notification_reference_id_and_type_must_be_set_together(db_session: AsyncSession):
    """Can't have reference_id without reference_type, or vice versa."""
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id="11111111-1111-1111-1111-111111111111",
        reference_type=None,
    )
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_invalid_notification_type_raises(db_session: AsyncSession):
    """notification_type is constrained to a known set of values."""
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, notification_type="typo_event")
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_invalid_reference_type_raises(db_session: AsyncSession):
    """reference_type is constrained to a known set of values."""
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, reference_type="not_a_real_type")
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_body_is_required(db_session: AsyncSession):
    """Every notification must have preview text — body cannot be null."""
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="announcement",
        body=None,
    )
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_image_is_optional(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, image_url=None)
    db_session.add(notification)
    await db_session.flush()

    assert notification.image_url is None


@pytest.mark.asyncio
async def test_notification_can_have_image(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    actor = make_user(email="actor@example.com")
    db_session.add_all([recipient, actor])
    await db_session.flush()

    notification = make_notification(
        recipient.id, org.id,
        actor_id=actor.id,
        image_url="https://cdn.example.com/avatars/ada.jpg",
    )
    db_session.add(notification)
    await db_session.flush()

    assert notification.image_url == "https://cdn.example.com/avatars/ada.jpg"


@pytest.mark.asyncio
async def test_notification_can_be_marked_read(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    from datetime import datetime, timezone
    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert notification.is_read is True
    assert notification.read_at is not None


@pytest.mark.asyncio
async def test_user_can_have_multiple_notifications(db_session: AsyncSession):
    _, org = await setup_org(db_session)

    recipient = make_user(email="recipient@example.com")
    db_session.add(recipient)
    await db_session.flush()

    db_session.add(make_notification(recipient.id, org.id, notification_type="liked_post", body="Ada liked your post"))
    db_session.add(make_notification(recipient.id, org.id, notification_type="new_message", body="New message from Ada", reference_type="message"))
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(Notification.user_id == recipient.id)
    )
    assert len(result.scalars().all()) == 2