"""
Chat models — GroupChatMessage (merged club/event group chats) and
DirectMessage (1:1 personal messaging).

GroupChatMessage design:
- conversation_type + conversation_id is a polymorphic pointer: type is
  one of 'club' or 'event', and conversation_id points to clubs.id or
  events.id depending on type. A single column can't carry a real FK to
  two different tables, so the DB only constrains conversation_type to a
  known set of values (CHECK constraint); validating that conversation_id
  actually exists in the right table happens in the app/service layer
  before insert — the standard, accepted cost of a polymorphic
  association, same pattern used by Discourse/GitHub-style comment
  systems.
- organization_id is a REAL FK, denormalized at write time from the
  owning club's or event's organization_id. This exists specifically so
  cross-org access can never happen by accident: without it, "does this
  message belong to my org" requires joining out to Club or Event before
  every query, and a service-layer route that forgets that join would
  silently leak one org's group chat history to another org's users —
  there is no DB-level constraint that would catch that mistake. With
  organization_id present and indexed, every query can (and must) filter
  on it directly, and the FK itself guarantees the value is always a
  real organization. ondelete=CASCADE — messages don't outlive their org.
- content is nullable (a message can be media-only), but content and
  media_url cannot both be null — a message needs some payload. Enforced
  via a CHECK constraint, not application logic, since the DB can
  guarantee this without a race condition.
- is_pinned / pinned_by / pinned_at: only one message can be pinned per
  conversation at a time. Enforced via a partial unique index on
  (conversation_type, conversation_id) WHERE is_pinned = true — the DB
  rejects a second pin attempt outright, no "check then set" race
  condition possible.
- Pinning/unpinning is restricted to admins/managers — that's a
  permission rule, not a structural constraint, so it's enforced in the
  service layer, not here. pinned_by is just an audit pointer to who
  pinned it.

SERVICE-LAYER REQUIREMENT (schema cannot enforce this — read before
writing any route that touches GroupChatMessage):
  Every read/write must verify the target club or event's
  organization_id matches the requesting user's organization_id BEFORE
  trusting organization_id on the message itself. organization_id here
  is a write-time snapshot for fast, safe querying — it is not a
  substitute for checking membership/permission on the underlying club
  or event at request time. Never accept a bare conversation_id from a
  client and trust it without that upstream check.

DirectMessage design:
- Personal 1:1 messaging between two users, separate from
  GroupChatMessage. Sender and recipient must belong to the same
  organization (enforced in the service layer).
- content is nullable, same content-or-media CHECK constraint as above.
- No pinning here — pinning only applies to group chats, per product
  decision.
- is_read / read_at track a single recipient's read state, which is the
  natural shape for a 1:1 conversation (unlike group chats, where each
  member would need independent read state — a different problem,
  intentionally not solved here).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GroupChatMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "group_chat_messages"

    # Real FK, denormalized at write time from the owning club's/event's
    # organization_id. See module docstring — this is what makes
    # org-scoped queries safe and fast without a join, but it is NOT a
    # substitute for checking club/event membership at the service layer.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Polymorphic scope — 'club' or 'event'. conversation_id points to
    # clubs.id or events.id depending on conversation_type; validated in
    # the service layer, not via DB FK (a column can't conditionally
    # reference two different tables).
    conversation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Pinning — only one pinned message per conversation, enforced by the
    # partial unique index in __table_args__. Who is allowed to pin is an
    # app-layer permission check (admin/manager only), not a DB rule.
    is_pinned: Mapped[bool] = mapped_column(nullable=False, default=False)
    pinned_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    pinned_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="group_chat_messages",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="group_chat_messages",
        foreign_keys=[user_id],
    )
    pinned_by_user: Mapped["User"] = relationship(
        "User",
        back_populates="pinned_group_chat_messages",
        foreign_keys=[pinned_by],
    )

    __table_args__ = (
        # Primary safe query path: "all messages in my org for this
        # conversation, in order" — never query without the org filter.
        Index(
            "ix_group_chat_messages_org_conversation_created_at",
            "organization_id",
            "conversation_type",
            "conversation_id",
            "created_at",
        ),
        Index(
            "uq_one_pinned_per_conversation",
            "conversation_type",
            "conversation_id",
            unique=True,
            postgresql_where=(is_pinned == True),  # noqa: E712
            sqlite_where=(is_pinned == True),  # noqa: E712
        ),
        CheckConstraint(
            "content IS NOT NULL OR media_url IS NOT NULL",
            name="ck_group_chat_message_content_or_media",
        ),
        CheckConstraint(
            "conversation_type IN ('club', 'event')",
            name="ck_group_chat_message_conversation_type",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupChatMessage id={self.id} org={self.organization_id} "
            f"{self.conversation_type}={self.conversation_id} user={self.user_id}>"
        )


class DirectMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "direct_messages"

    # Sender and recipient must belong to the same org — enforced in the
    # service layer, the DB only stores the scope.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Content
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Read receipt — single recipient, so a plain boolean + timestamp is
    # sufficient (unlike group chats, which would need per-member read
    # state).
    is_read: Mapped[bool] = mapped_column(nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="direct_messages",
        foreign_keys=[organization_id],
    )
    sender: Mapped["User"] = relationship(
        "User",
        back_populates="sent_direct_messages",
        foreign_keys=[sender_id],
    )
    recipient: Mapped["User"] = relationship(
        "User",
        back_populates="received_direct_messages",
        foreign_keys=[recipient_id],
    )

    __table_args__ = (
        Index(
            "ix_direct_messages_sender_recipient_created_at",
            "sender_id",
            "recipient_id",
            "created_at",
        ),
        Index(
            "ix_direct_messages_recipient_sender_created_at",
            "recipient_id",
            "sender_id",
            "created_at",
        ),
        CheckConstraint(
            "content IS NOT NULL OR media_url IS NOT NULL",
            name="ck_direct_message_content_or_media",
        ),
    )

    def __repr__(self) -> str:
        return f"<DirectMessage id={self.id} sender={self.sender_id} recipient={self.recipient_id}>"