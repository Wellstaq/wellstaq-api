"""
Hashtag model — a tag that posts can be associated with.

Design principles:
- tag_name is unique — no duplicate hashtags.
- total_post_count is denormalized: increment on every new post that uses
  this tag, decrement if a post using it is deleted. Avoids counting rows
  live when rendering trending/explore views.
- Actual trend scoring (e.g. velocity, decay) lives in a separate
  hashtag_trend_snapshots table/model, not here.
"""

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from datetime import datetime
from decimal import Decimal

class Hashtag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hashtags"
 
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
 
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
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
    )
 
    def __repr__(self) -> str:
        return f"<Hashtag id={self.id} org={self.organization_id} tag_name={self.tag_name}>"
"""
HashtagTrendSnapshot model — periodic computed trend score for a hashtag
within an organization.

Design principles:
- The backend computes trend_score on a schedule (every 15 or 60 minutes)
  — never derived live. Same denormalized-for-speed pattern as
  Department.member_count and DepartmentRankSnapshot: the frontend reads
  the latest snapshot only, it never aggregates raw post data live.
- Scoped per organization — trending is computed within an org's own
  feed, not globally across all orgs.
- window_start / window_end describe the measured period; computed_at is
  when the backend actually ran the calculation (may lag slightly behind
  window_end).
- No uniqueness constraint on (hashtag_id, organization_id, window_end) —
  snapshots accumulate over time, same pattern as
  DepartmentRankSnapshot, one row per computation run.
"""



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
    trend_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0)
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
        Index(
            "ix_hashtag_trend_snapshots_org_window_end",
            "organization_id",
            "window_end",
        ),
        Index(
            "ix_hashtag_trend_snapshots_hashtag_window_end",
            "hashtag_id",
            "window_end",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<HashtagTrendSnapshot id={self.id} hashtag={self.hashtag_id} "
            f"score={self.trend_score}>"
        )