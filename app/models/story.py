"""
Story model — ephemeral, expiring user content (Facebook/Instagram-style).

Design principles:
- organization_id is required (NOT NULL), ondelete=CASCADE — same
  tenant-isolation pattern as Post/Hashtag/GroupChatMessage: stories are
  always scoped to the poster's org, and denormalizing it here means
  org-scoped queries ("active stories in my org's feed") never need a
  join through users.
- media_type mirrors Post's pattern: free string (image | video),
  validated at the app layer, no DB enum — keeps it extensible without
  a migration if a new media type is added later.
- No view tracking yet (no StoryView table) — per decision, this is
  intentionally out of scope for now. If added later, it'll be a
  separate join table (user_id, story_id, viewed_at), same shape as
  EventParticipant/ChallengeParticipant, not columns on Story itself.
- expires_at is set at creation time by the app layer (e.g.
  created_at + 24 hours) — this model doesn't compute it.
- Expiry is enforced by a PURGE, not just a query filter: expired rows
  are actually deleted by a periodic background job (see the query
  snippet below), not just hidden via WHERE expires_at > now() and left
  to accumulate. This keeps the table small and the (user_id,
  expires_at) index cheap, since ephemeral content has no long-term
  query value once expired.
- No updated_at — a story's content doesn't get edited after posting,
  mirrors Club's "append-only, no updated_at" reasoning. created_at only.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin


class Story(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "stories"

    # ── Author ────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Org scope ─────────────────────────────────────────────────────────
    # Required, denormalized — same tenant-isolation pattern as Post,
    # Hashtag, GroupChatMessage. Set by the service layer as a copy of
    # the posting user's organization_id at creation time.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────
    media_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # image | video — free string, validated at app layer, matches Post
    media_type: Mapped[str] = mapped_column(String(50), nullable=False, default="image")

    # ── Lifecycle ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    # Set by the app layer at creation time (e.g. created_at + 24h).
    # Actual deletion happens via a periodic purge job, not a query
    # filter — see module docstring.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="stories",
        foreign_keys=[user_id],
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="stories",
        foreign_keys=[organization_id],
    )

    __table_args__ = (
        # Primary query pattern: "this user's active stories" / purge job
        # scanning for expired rows across all users.
        Index("ix_stories_user_expires_at", "user_id", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<Story id={self.id} user={self.user_id} expires_at={self.expires_at}>"

