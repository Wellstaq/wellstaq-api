"""
Notification model tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed — these are model/ORM-level tests.

Factories and org/user setup come exclusively from conftest.py
(make_user, make_org_with_owner) — no local duplicates.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from tests.conftest import make_org_with_owner, make_user


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Basic persistence ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notification_persists(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    actor = make_user(org.id, email="actor@example.com", branch_id=owner.branch_id)
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
    assert fetched.read_at is None


@pytest.mark.asyncio
async def test_notification_id_is_uuid_format(db_session: AsyncSession):
    """ID must be a valid, parseable UUID — not just the right string shape.
    String-shape checks ([8,4,4,4,12]) pass values like
    'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'; UUID() actually validates it."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    from uuid import UUID
    assert str(UUID(notification.id)) == notification.id


# ── Nullable fields ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notification_actor_can_be_null_for_system_generated(db_session: AsyncSession):
    """System-generated notifications (e.g. event reminders) have no
    triggering user."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
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
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
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
async def test_notification_image_is_optional(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, image_url=None)
    db_session.add(notification)
    await db_session.flush()

    assert notification.image_url is None


@pytest.mark.asyncio
async def test_notification_can_have_image(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    actor = make_user(org.id, email="actor@example.com", branch_id=owner.branch_id)
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
async def test_notification_can_be_system_wide_with_no_organization(db_session: AsyncSession):
    """organization_id is nullable — a system notification has no org
    scope and no triggering actor."""
    # A system-wide recipient still needs an org to satisfy User.organization_id NOT NULL.
    # The notification itself carries no org reference.
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(
        org.id, email="system_recipient@example.com", branch_id=owner.branch_id
    )
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=None,
        actor_id=None,
        notification_type="announcement",
        body="Scheduled maintenance tonight at 10pm",
    )
    db_session.add(notification)
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(Notification.id == notification.id)
    )
    fetched = result.scalar_one()
    assert fetched.organization_id is None
    assert fetched.actor_id is None
    assert fetched.user_id == recipient.id


@pytest.mark.asyncio
async def test_system_notifications_are_filterable_by_null_org(db_session: AsyncSession):
    """The whole point of nullable organization_id is to support querying
    system notifications separately from org-scoped ones."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    # One system notification, one org-scoped notification
    db_session.add(Notification(
        user_id=recipient.id,
        organization_id=None,
        notification_type="announcement",
        body="Platform maintenance tonight",
    ))
    db_session.add(make_notification(recipient.id, org.id, body="Ada liked your post"))
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(
            Notification.user_id == recipient.id,
            Notification.organization_id.is_(None),
        )
    )
    system_notifications = result.scalars().all()
    assert len(system_notifications) == 1
    assert system_notifications[0].body == "Platform maintenance tonight"


# ── Read state ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notification_can_be_marked_read(db_session: AsyncSession):
    """Marking a notification read requires both is_read=True and read_at set."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert notification.is_read is True
    assert notification.read_at is not None


@pytest.mark.asyncio
async def test_notification_read_at_without_is_read_raises(db_session: AsyncSession):
    """is_read=False with read_at set violates the consistency constraint."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id="11111111-1111-1111-1111-111111111111",
        reference_type="post",
        is_read=False,
        read_at=datetime.now(timezone.utc),  # inconsistent — is_read is still False
    )
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_is_read_true_without_read_at_raises(db_session: AsyncSession):
    """is_read=True without read_at set violates the consistency constraint."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id="11111111-1111-1111-1111-111111111111",
        reference_type="post",
        is_read=True,
        read_at=None,  # inconsistent — is_read is True but no timestamp
    )
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_valid_unread_state(db_session: AsyncSession):
    """Explicit test for the valid unread state: is_read=False, read_at=None."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    assert notification.is_read is False
    assert notification.read_at is None


@pytest.mark.asyncio
async def test_notification_valid_read_state(db_session: AsyncSession):
    """Explicit test for the valid read state: is_read=True, read_at set."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id="11111111-1111-1111-1111-111111111111",
        reference_type="post",
        is_read=True,
        read_at=datetime.now(timezone.utc),
    )
    db_session.add(notification)
    await db_session.flush()

    assert notification.is_read is True
    assert notification.read_at is not None


# ── Constraint enforcement ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notification_reference_id_and_type_must_be_set_together(db_session: AsyncSession):
    """Can't have reference_id without reference_type, or vice versa."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id="11111111-1111-1111-1111-111111111111",
        reference_type=None,  # missing — should raise
    )
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_reference_type_without_reference_id_raises(db_session: AsyncSession):
    """The reverse direction: reference_type set but reference_id null also
    violates the consistency constraint."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = Notification(
        user_id=recipient.id,
        organization_id=org.id,
        notification_type="liked_post",
        body="Ada liked your post",
        reference_id=None,   # missing — should raise
        reference_type="post",
    )
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_invalid_notification_type_raises(db_session: AsyncSession):
    """notification_type is constrained to a known set of values."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, notification_type="typo_event")
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_invalid_reference_type_raises(db_session: AsyncSession):
    """reference_type is constrained to a known set of values."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, reference_type="not_a_real_type")
    db_session.add(notification)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_notification_body_is_required(db_session: AsyncSession):
    """Every notification must have preview text — body cannot be null."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
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


# ── Relationships and cascades ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_can_have_multiple_notifications(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    db_session.add(make_notification(
        recipient.id, org.id,
        notification_type="liked_post",
        body="Ada liked your post",
    ))
    db_session.add(make_notification(
        recipient.id, org.id,
        notification_type="new_message",
        body="New message from Ada",
        reference_id="33333333-3333-3333-3333-333333333333",
        reference_type="message",
    ))
    await db_session.flush()

    result = await db_session.execute(
        select(Notification).where(Notification.user_id == recipient.id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_deleting_user_cascades_to_notifications(db_session: AsyncSession):
    """Notifications are meaningless without their recipient — deleting a
    User must cascade and remove all their notifications."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    notification_id = notification.id

    # Use raw SQL to bypass ORM cascade and let the DB enforce it
    await db_session.execute(
        text("DELETE FROM users WHERE id = :uid"),
        {"uid": recipient.id},
    )
    await db_session.flush()

    # Raw SQL bypasses the ORM identity map — expire_all() forces a
    # fresh DB read so the deleted row isn't returned from cache.
    db_session.expire_all()

    result = await db_session.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_actor_deleted_sets_actor_id_null(db_session: AsyncSession):
    """Deleting the actor (liker, commenter) should SET NULL on
    actor_id — the notification remains visible to the recipient, just
    without attribution."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    actor = make_user(org.id, email="actor@example.com", branch_id=owner.branch_id)
    db_session.add_all([recipient, actor])
    await db_session.flush()

    notification = make_notification(recipient.id, org.id, actor_id=actor.id)
    db_session.add(notification)
    await db_session.flush()

    notification_id = notification.id
    actor_id = actor.id

    await db_session.execute(
        text("DELETE FROM users WHERE id = :uid"),
        {"uid": actor_id},
    )
    await db_session.flush()

    # Raw SQL bypasses the ORM, so the session identity map still holds
    # the stale Notification object with the old actor_id. expire_all()
    # forces SQLAlchemy to re-fetch from the DB on next access.
    db_session.expire_all()

    result = await db_session.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    fetched = result.scalar_one()
    assert fetched.actor_id is None


@pytest.mark.asyncio
async def test_deleting_organization_cascades_to_notifications(db_session: AsyncSession):
    """Deleting an Organization must cascade and remove all its scoped
    notifications — organization_id FK is CASCADE."""
    org, owner = await make_org_with_owner(db_session)

    recipient = make_user(org.id, email="recipient@example.com", branch_id=owner.branch_id)
    db_session.add(recipient)
    await db_session.flush()

    notification = make_notification(recipient.id, org.id)
    db_session.add(notification)
    await db_session.flush()

    notification_id = notification.id

    await db_session.execute(
        text("DELETE FROM organizations WHERE id = :oid"),
        {"oid": org.id},
    )
    await db_session.flush()
    db_session.expire_all()

    result = await db_session.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cross_org_actor_is_allowed_at_db_level(db_session: AsyncSession):
    """The DB cannot enforce that actor and recipient belong to the same org —
    that responsibility lives in the service layer. This test documents that
    intentional gap so future developers don't assume a constraint exists.

    Scenario: recipient is in org_a, actor is in org_b. The notification
    is created successfully — no IntegrityError — because the DB only
    validates that actor_id references a real user row, not that the user
    belongs to the same org as the recipient."""
    org_a, owner_a = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, owner_b = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )

    recipient = make_user(org_a.id, email="recipient@example.com", branch_id=owner_a.branch_id)
    actor = make_user(org_b.id, email="actor@example.com", branch_id=owner_b.branch_id)  # different org
    db_session.add_all([recipient, actor])
    await db_session.flush()

    # This must succeed — the service layer is responsible for preventing
    # cross-org actor assignments, not the DB.
    notification = make_notification(recipient.id, org_a.id, actor_id=actor.id)
    db_session.add(notification)
    await db_session.flush()  # no IntegrityError

    assert notification.actor_id == actor.id