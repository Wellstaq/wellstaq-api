"""
Notification model — alerts a user needs to see, distinct from
user_activity_feed (which logs what the user themself did). This is what
was done TO/AROUND them.

Design principles:
- organization_id is nullable: this is what distinguishes a SYSTEM
  notification (platform-wide, not scoped to any single org — e.g. "Your
  account was created", a platform maintenance notice, or a broadcast
  sent to every user regardless of org) from an ORGANIZATION notification
  (e.g. "Ada liked your post", "New department announcement" — generated
  within and scoped to one tenant). A notification's actual tenant
  isolation is enforced by user_id (the recipient already belongs to
  exactly one org); organization_id here is denormalized context for
  filtering/audit (e.g. org deletion cascading to its notifications), not
  the sole isolation mechanism.

  No branch_id here, deliberately: a notification is a message about the
  recipient, not an event that happened at a location. "Notifications for
  Branch X" isn't a real product query the way "activity logged at Branch
  X" is — and if it's ever needed, it's reachable via
  Notification.user_id -> User.branch_id without denormalizing a column
  that would need to be kept in sync on every notification-creation call
  site.
- actor_id = who triggered this notification (who liked/commented/
  messaged). Nullable — system-generated notifications (e.g. scheduled
  event reminders, or any organization_id IS NULL system notification)
  have no triggering user.
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
  via a future migration that updates the allowed list. "announcement"
  covers both an org-level announcement (organization_id set) and a
  platform-wide one (organization_id null) — the type doesn't change,
  the scope does.
- body is required — every notification must have preview text, even if
  it's brief. Max 500 chars — sufficient for current types; if
  multilingual or dynamic templates ("X and 3 others liked your post")
  are introduced, revisit this limit. image_url is optional (e.g.
  liker's avatar, event cover photo); plenty of notifications
  (text-only announcements) won't have one.
- is_read / read_at track whether the recipient has seen it.
  A CHECK constraint enforces consistency: is_read=True requires
  read_at to be set, and is_read=False requires read_at to be NULL.
  This prevents silent bad state (unread count bugs, stale timestamps).

Tenant isolation note: cross-org leakage isn't possible through this
table structurally, since every notification has exactly one user_id
recipient (a user belongs to exactly one org). The app/service layer is
still responsible for ensuring actor_id (when set) makes sense in
context — e.g. an org-scoped "liked_post" notification's actor should
belong to the same org as the recipient. The DB can't enforce that two
independent FKs agree on a third column without a trigger; same accepted
cost as the PostHashtag / hashtag-organization-match check.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.constants import NOTIFICATION_TYPES, REFERENCE_TYPES



class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"

    # Recipient — tenant isolation lives here, not on organization_id
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        # NOTE: no index=True here — user_id is the leading column of the
        # composite index ix_notifications_user_is_read_created_at below,
        # which already covers single-column lookups on user_id. A
        # redundant standalone index would waste write overhead.
    )

    # Nullable — null means this is a SYSTEM/platform-wide notification,
    # not scoped to any single organization. Set means it's an
    # ORGANIZATION notification generated within that tenant's context.
    organization_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
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

    # Read state — is_read and read_at must be consistent (see CHECK
    # constraint ck_notification_read_consistency in __table_args__).
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
        # Primary read query pattern: fetch unread notifications for a
        # user, ordered by recency. user_id leading so Postgres/SQLite
        # can seek directly to one user's rows without a full scan.
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
        # reference_id and reference_type must be set together or both
        # null — never one without the other.
        CheckConstraint(
            "(reference_id IS NULL AND reference_type IS NULL) "
            "OR (reference_id IS NOT NULL AND reference_type IS NOT NULL)",
            name="ck_notification_reference_id_type_together",
        ),
       
        CheckConstraint(
            "(is_read = false AND read_at IS NULL) OR (is_read = true AND read_at IS NOT NULL)",
            name="ck_notification_read_consistency",
        ),
       
        CheckConstraint(
            "length(body) <= 500",
            name="ck_notification_body_max_length",
        ),
    )

    def __repr__(self) -> str:
        scope = self.organization_id or "system"
        return (
            f"<Notification id={self.id} user={self.user_id} "
            f"scope={scope} type={self.notification_type}>"
        )