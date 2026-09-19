"""One like per user per story."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StoryLike(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "story_likes"

    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    story_id: Mapped[str] = mapped_column(String(36), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    story: Mapped["Story"] = relationship("Story", back_populates="likes")

    __table_args__ = (UniqueConstraint("story_id", "user_id", name="uq_story_likes_story_user"),)
