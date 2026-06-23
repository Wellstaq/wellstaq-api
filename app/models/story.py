"""
Story model — ephemeral, expiring user content (Facebook/Instagram-style).

Design principles:
- organization_id is required (NOT NULL), ondelete=CASCADE — same
  tenant-isolation pattern as Post/Hashtag/GroupChatMessage: stories are
  always scoped to the poster's org, and denormalizing it here means
  org-scoped queries ("active stories in my org's feed") never need a
  join through users.
- media_type mirrors Post's pattern: free string (image | video),
  validated at the app layer AND enforced at the DB layer via a
  CheckConstraint — belt-and-suspenders, since this table is high-write
  and a bad migration/raw insert should not be able to introduce garbage.
  CheckConstraint works identically on SQLite and PostgreSQL, so it's
  safe across test and production environments.
- No view tracking yet (no StoryView table) — per decision, this is
  intentionally out of scope for now. If added later, it'll be a
  separate join table (user_id, story_id, viewed_at), same shape as
  EventParticipant/ChallengeParticipant, not columns on Story itself.
- expires_at is set at creation time by the app layer (e.g.
  created_at + 24 hours) — this model doesn't compute it. A
  CheckConstraint (expires_at > created_at) guards against the bug
  class where that computation goes wrong (e.g. subtracts instead of
  adds) and produces an already-expired or backwards-dated row.
- Expiry is enforced by a PURGE, not just a query filter: expired rows
  are actually deleted by a periodic background job, not just hidden
  via WHERE expires_at > now() and left to accumulate. This keeps the
  table small and the (user_id, expires_at) index cheap, since
  ephemeral content has no long-term query value once expired.
  The purge job remains an operational dependency outside this schema —
  monitor/alert on it in production.
  NOTE: a DB-generated is_expired column (via Computed(..., persisted=False))
  was considered to avoid trusting app-layer datetime.now() calculations,
  but Computed/generated columns aren't reliably portable to SQLite (used
  in this project's test suite), so it was dropped in favor of keeping
  expiry filtering at the app/query layer (WHERE expires_at > :now) for
  now. Revisit once integration tests run against Postgres directly.
- Uses the project's shared TimestampMixin (created_at + updated_at,
  Python-side defaults for SQLite compatibility — see app/models/base.py)
  for consistency with every other model, rather than a one-off
  created_at column. updated_at exists on this table as a result but is
  not expected to change after insert — a story's content isn't edited
  after posting. If that ever stops being true (e.g. captions become
  editable), updated_at is already there to support it for free.
- Two composite indexes support the two real query patterns:
    1. (user_id, expires_at) — "this user's active stories" / purge job
       scanning for expired rows.
    2. (organization_id, expires_at) — "active stories in my org's feed",
       the org-scoped feed query the tenant-isolation docstring above
       promises. A single-column index on organization_id alone isn't
       enough once expires_at is also filtered on.
- Primary key uses UUIDv7 via UUIDPrimaryKeyMixin (project-wide
  convention for high-write tables — time-sortable, avoids B-tree
  fragmentation on inserts, unlike UUIDv4).
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
    # image | video — free string, app-validated AND DB-enforced via
    # CheckConstraint below (belt-and-suspenders for a high-write table)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False, default="image")

    # ── Lifecycle ─────────────────────────────────────────────────────────
    # created_at / updated_at come from TimestampMixin.
    #
    # Set by the app layer at creation time (e.g. created_at + 24h).
    # Actual deletion happens via a periodic purge job, not a query
    # filter — see module docstring. Active-vs-expired filtering is done
    # at query time: WHERE expires_at > :now.
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
        # "This user's active stories" / purge job scanning for expired rows
        Index("ix_stories_user_expires_at", "user_id", "expires_at"),
        # "Active stories in my org's feed" — the org-scoped query the
        # tenant-isolation docstring above promises; needed because a
        # single-column index on organization_id alone doesn't serve a
        # range filter on expires_at efficiently.
        Index("ix_stories_org_expires_at", "organization_id", "expires_at"),
        # DB-level enforcement, not just app-layer validation. Works on
        # both SQLite (tests) and PostgreSQL (production).
        CheckConstraint("media_type IN ('image', 'video')", name="ck_stories_media_type"),
        # Guards against a bad expires_at computation (e.g. subtracting
        # instead of adding the TTL) producing an already-expired or
        # backwards-dated row at insert time.
        CheckConstraint("expires_at > created_at", name="ck_stories_expires_after_created"),
    )

    def __repr__(self) -> str:
        return f"<Story id={self.id} user={self.user_id} expires_at={self.expires_at}>"