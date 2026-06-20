"""
Notification model — alerts a user needs to see, distinct from
user_activity_feed (which logs what the user themself did). This is what
was done TO/AROUND them.

Design principles:
- actor_id = who triggered this notification (who liked/commented/
  messaged). Nullable — system-generated notifications (e.g. scheduled
  event reminders) have no triggering user.
- reference_id / reference_type point to the exact related record, same
  polymorphic pattern as GroupChatMessage.conversation_type — a single
  column can't carry a real FK to multiple different tables, so
  validating reference_id actually exists in the right table happens in
  the app/service layer, not the DB. Both nullable together: some
  notification types (e.g. a broadcast announcement) have no underlying
  record at all. A CHECK constraint enforces they're either both null or
  both set — never just one.
- notification_type and reference_type are constrained to known sets of
  values via CHECK constraints, same approach as conversation_type on
  GroupChatMessage — guards against typos while staying easy to extend
  via a future migration that updates the allowed list.
- body is required — every notification must have preview text, even if
  it's brief. image_url is optional (e.g. liker's avatar, event cover
  photo); plenty of notifications (text-only announcements) won't have
  one.
- is_read / read_at track whether the recipient has seen it.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

NOTIFICATION_TYPES = (
    "liked_post",
    "commented_post",
    "new_message",
    "joined_club",
    "joined_department",
    "event_reminder",
    "challenge_completed",
    "announcement",
)

REFERENCE_TYPES = (
    "post",
    "comment",
    "message",
    "event",
    "challenge",
    "club",
    "department",
)


class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"

    # Recipient
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable — system-generated notifications have no triggering user
    actor_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Polymorphic pointer — validated against the right table in the
    # service layer, not via DB FK. Both null together for notifications
    # with no underlying record (e.g. a broadcast announcement).
    reference_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Content — body is required, image is optional
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Read state
    is_read: Mapped[bool] = mapped_column(nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="notifications",
        foreign_keys=[user_id],
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="notifications",
        foreign_keys=[organization_id],
    )
    actor: Mapped["User"] = relationship(
        "User",
        back_populates="triggered_notifications",
        foreign_keys=[actor_id],
    )

    __table_args__ = (
        Index(
            "ix_notifications_user_is_read_created_at",
            "user_id",
            "is_read",
            "created_at",
        ),
        CheckConstraint(
            f"notification_type IN ({', '.join(repr(t) for t in NOTIFICATION_TYPES)})",
            name="ck_notification_type",
        ),
        CheckConstraint(
            f"reference_type IS NULL OR reference_type IN ({', '.join(repr(t) for t in REFERENCE_TYPES)})",
            name="ck_notification_reference_type",
        ),
        CheckConstraint(
            "(reference_id IS NULL AND reference_type IS NULL) "
            "OR (reference_id IS NOT NULL AND reference_type IS NOT NULL)",
            name="ck_notification_reference_id_type_together",
        ),
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id} user={self.user_id} type={self.notification_type}>"