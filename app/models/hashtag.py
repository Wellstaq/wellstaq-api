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

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Hashtag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hashtags"

    tag_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    total_post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ──────────────────────────────────────────────────────
    post_hashtags: Mapped[list["PostHashtag"]] = relationship(
        "PostHashtag",
        back_populates="hashtag",
    )

    def __repr__(self) -> str:
        return f"<Hashtag id={self.id} tag_name={self.tag_name}>"