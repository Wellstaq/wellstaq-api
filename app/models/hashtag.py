"""
Hashtag model — a tag that posts can be associated with.

Design principles:
- tag_name is unique per organization (UniqueConstraint on
  organization_id + tag_name), not globally — the same tag can exist
  independently in different orgs with separate post counts and trend
  scores.
- tag_name is enforced lowercase and non-empty at the DB level via
  CheckConstraints. Normalization (stripping leading '#', lowercasing,
  trimming whitespace) is the service layer's responsibility before
  insert; the DB constraints are the last line of defence. Note: the
  DB enforces tag_name = LOWER(tag_name) and length(trim(tag_name)) > 0,
  but does NOT enforce that leading/trailing spaces are stripped — e.g.
  " fitness" and "fitness" are treated as different tags. The service
  layer must call strip() before insert to prevent this.
- total_post_count is denormalized: increment on every new post that uses
  this tag, decrement if a post using it is deleted. Avoids counting rows
  live when rendering trending/explore views. A CheckConstraint prevents
  it going negative (which a decrement bug or race condition could cause).
- Actual trend scoring (e.g. velocity, decay) lives in
  HashtagTrendSnapshot, not here.

HashtagTrendSnapshot model — periodic computed trend score for a hashtag
within an organization.

Design principles:
- The backend computes trend_score on a schedule (every 15 or 60 minutes)
  — never derived live. Same denormalized-for-speed pattern as
  Department.member_count and DepartmentRankSnapshot: the frontend reads
  the latest snapshot only, it never aggregates raw post data live.
- Scoped per organization — trending is computed within an org's own
  feed, not globally across all orgs. Every snapshot carries its own
  organization_id so queries can filter by org without joining back to
  the Hashtag row.
- organization_id is denormalized for query speed and must match the
  organization_id of the referenced Hashtag row. The DB cannot enforce
  this (it would require a trigger or composite FK); the service layer is
  responsible for ensuring consistency on every insert. Same accepted cost
  as the Notification model's cross-org actor limitation — documented
  here explicitly so future developers don't assume a constraint exists.
- window_start / window_end describe the measured period; a
  CheckConstraint enforces window_end > window_start to guard against
  miscalculated windows corrupting trend queries.
- computed_at is when the backend actually ran the calculation (may lag
  slightly behind window_end). No default — the background job must
  supply this explicitly so the timestamp reflects actual computation
  time, not row-insert time.
- trend_score uses Numeric(10, 4) — max 999999.9999. Revisit precision
  once the scoring algorithm is finalized (e.g. normalize to 0–100 or
  0.0–1.0).
- post_count is non-negative enforced at DB level — same rationale as
  Hashtag.total_post_count.
- No uniqueness constraint on (hashtag_id, organization_id, window_end) —
  snapshots accumulate over time, same pattern as DepartmentRankSnapshot,
  one row per computation run.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Hashtag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hashtags"

    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stored lowercase — service layer normalizes before insert,
    # CheckConstraint is the DB-level safety net.
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Denormalized counter — incremented/decremented by the service layer.
    # Non-negative enforced at DB level; a decrement bug should raise, not
    # silently produce -1.
    total_post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="hashtags",
        foreign_keys=[organization_id],
    )
    post_hashtags: Mapped[list["PostHashtag"]] = relationship(
        "PostHashtag",
        back_populates="hashtag",
    )
    trend_snapshots: Mapped[list["HashtagTrendSnapshot"]] = relationship(
        "HashtagTrendSnapshot",
        back_populates="hashtag",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "tag_name", name="uq_hashtag_org_tag_name"),
        # Enforce lowercase storage — prevents "Fitness" and "fitness"
        # from being treated as different tags and splitting trend data.
        CheckConstraint(
            "tag_name = LOWER(tag_name)",
            name="ck_hashtag_tag_name_lowercase",
        ),
        # Reject empty strings and whitespace-only values — a tag of ""
        # or "   " is meaningless. trim() catches the whitespace case;
        # the service layer is still responsible for stripping before
        # insert to prevent " fitness" vs "fitness" being stored as
        # separate tags (the DB can't enforce that more broadly).
        CheckConstraint(
            "length(trim(tag_name)) > 0",
            name="ck_hashtag_tag_name_not_empty",
        ),
        # Prevent negative counts from decrement bugs or race conditions.
        CheckConstraint(
            "total_post_count >= 0",
            name="ck_hashtag_total_post_count_non_negative",
        ),
    )

    def __repr__(self) -> str:
        return f"<Hashtag id={self.id} org={self.organization_id} tag_name={self.tag_name}>"


class HashtagTrendSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hashtag_trend_snapshots"

    hashtag_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        nullable=False,
    )

    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Numeric(10, 4) = max 999999.9999. Revisit once scoring algorithm
    # is finalized (normalize to 0–100, 0.0–1.0, or unbounded velocity).
    trend_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0)

    # Explicitly supplied by the background job — no default. Reflects
    # actual computation time, not row-insert time.
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    hashtag: Mapped["Hashtag"] = relationship(
        "Hashtag",
        back_populates="trend_snapshots",
        foreign_keys=[hashtag_id],
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="hashtag_trend_snapshots",
        foreign_keys=[organization_id],
    )

    __table_args__ = (
        # "Latest trending hashtags for this org" — primary read query.
        Index(
            "ix_hashtag_trend_snapshots_org_window_end",
            "organization_id",
            "window_end",
        ),
        # "All trend history for this hashtag" — secondary read query.
        # hashtag_id is the leading column so single-column lookups on
        # hashtag_id are covered without a separate standalone index.
        Index(
            "ix_hashtag_trend_snapshots_hashtag_window_end",
            "hashtag_id",
            "window_end",
        ),
        # Guard against miscalculated windows corrupting trend queries.
        CheckConstraint(
            "window_end > window_start",
            name="ck_hashtag_trend_snapshot_window_order",
        ),
        # Prevent negative post counts from background job bugs.
        CheckConstraint(
            "post_count >= 0",
            name="ck_hashtag_trend_snapshot_post_count_non_negative",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<HashtagTrendSnapshot id={self.id} hashtag={self.hashtag_id} "
            f"score={self.trend_score}>"
        )