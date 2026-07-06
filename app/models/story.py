"""
Story model — ephemeral, expiring user content (Facebook/Instagram-style).

Design principles:
- organization_id is required (NOT NULL), ondelete=CASCADE — same
  tenant-isolation pattern as Post/Hashtag/GroupChatMessage.
- branch_id is NULLABLE — a user chooses between posting to their branch
  feed (branch_id set) or the org-wide feed (branch_id null). This
  mirrors Post.branch_id, which follows the same pattern.
  ondelete=SET NULL: if a branch is deleted, its stories are NOT deleted —
  they survive as org-level stories (branch_id becomes null). This is
  intentional: ephemeral content already has an expiry; losing the branch
  association is a softer consequence than losing the content itself.
- media_type is free string (image | video), validated at app layer AND
  enforced at DB layer via CheckConstraint — belt-and-suspenders for a
  high-write table.
- expires_at is set at creation time by the app layer (e.g.
  created_at + 24 hours). CheckConstraint (expires_at > created_at)
  guards against bad computations producing an already-expired row.
- Expiry is enforced by a PURGE job, not a query filter — expired rows
  are deleted by a periodic background job, not just hidden.
- Two composite indexes support the two real query patterns:
    1. (user_id, expires_at) — "this user's active stories" / purge job
    2. (organization_id, expires_at) — "active stories in my org's feed"
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Story(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stories"

    # ── Author ────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Org scope (required) ──────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Branch scope (optional) ───────────────────────────────────────────
    # null  = posted to the org-wide feed
    # set   = posted to a specific branch's feed
    # ondelete=SET NULL: branch deletion does not destroy the story —
    # it becomes an org-level story instead (branch_id → null).
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────
    media_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False, default="image")

    # ── Lifecycle ─────────────────────────────────────────────────────────
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
    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="stories",
        foreign_keys=[branch_id],
    )

    __table_args__ = (
        Index("ix_stories_user_expires_at", "user_id", "expires_at"),
        Index("ix_stories_org_expires_at", "organization_id", "expires_at"),
        CheckConstraint("media_type IN ('image', 'video')", name="ck_stories_media_type"),
        CheckConstraint("expires_at > created_at", name="ck_stories_expires_after_created"),
    )

    def __repr__(self) -> str:
        return f"<Story id={self.id} user={self.user_id} expires_at={self.expires_at}>"