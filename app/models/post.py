"""
Post model — a feed post, plus its related social-graph tables.

Design principles:
- Every post belongs to an org-level feed (organization_id always set),
  even general (non-club, non-branch) posts.
- branch_id is nullable: null = org-level feed post, not branch-specific.
- club_id is nullable: null = general feed post, not tied to a club.
- like_count / comment_count are denormalized on Post so feed rendering
  never has to count rows live — increment/decrement them alongside the
  PostLike / PostComment inserts/deletes.
- FK columns that are the *leading* column of a composite index declared
  in __table_args__ do NOT also get index=True — that would create a
  redundant duplicate index, since a composite index can already serve
  single-column lookups on its leftmost column.
- PostComment.parent_comment_id is a self-referential FK: null = top-level
  comment, set = reply to another comment.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Post(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "posts"

    # Author
    # No separate index=True: covered by the (user_id, created_at) composite
   
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Feed scoping
    # Every post belongs to an org feed, even general (non-club) posts.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Nullable — null means this post sits at the org-level feed, not a
    # specific branch's feed.
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Nullable — null means this is a general feed post, not club-specific.
    club_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=True,
    )

    # ── Content ───────────────────────────────────────────────────────────
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Denormalized counters ─────────────────────────────────────────────
    # Increment on like/comment insert, decrement on unlike/delete — avoids
    # counting rows live when rendering the feed.
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="posts",
        foreign_keys=[user_id],
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="posts",
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="posts",
        foreign_keys=[branch_id],
    )

    club: Mapped["Club"] = relationship(
        "Club",
        back_populates="posts",
        foreign_keys=[club_id],
    )

    post_hashtags: Mapped[list["PostHashtag"]] = relationship(
        "PostHashtag",
        back_populates="post",
        cascade="all, delete-orphan",
    )

    likes: Mapped[list["PostLike"]] = relationship(
        "PostLike",
        back_populates="post",
        cascade="all, delete-orphan",
    )

    comments: Mapped[list["PostComment"]] = relationship(
        "PostComment",
        back_populates="post",
        cascade="all, delete-orphan",
    )

    shares: Mapped[list["PostShare"]] = relationship(
        "PostShare",
        back_populates="post",
        cascade="all, delete-orphan",
    )

    saves: Mapped[list["PostSave"]] = relationship(
        "PostSave",
        back_populates="post",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_posts_org_created_at", "organization_id", "created_at"),
        Index("ix_posts_club_created_at", "club_id", "created_at"),
        Index("ix_posts_branch_created_at", "branch_id", "created_at"),
        Index("ix_posts_user_created_at", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Post id={self.id} user={self.user_id} org={self.organization_id}>"


class PostHashtag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "post_hashtags"

    # Single-column indexes — no composite covers either of these alone,
    # so index=True is kept on both.
    post_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    hashtag_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="post_hashtags",
        foreign_keys=[post_id],
    )

    hashtag: Mapped["Hashtag"] = relationship(
        "Hashtag",
        back_populates="post_hashtags",
        foreign_keys=[hashtag_id],
    )

    def __repr__(self) -> str:
        return f"<PostHashtag post={self.post_id} hashtag={self.hashtag_id}>"


class PostLike(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One like per user per post — liking inserts a row, unliking deletes
    it. The unique constraint on (post_id, user_id) makes duplicate likes
    impossible. Total likes = row count, cached on Post.like_count."""

    __tablename__ = "post_likes"

    # No separate index=True on post_id — it's the leading column of the
    # unique constraint below, which already indexes it.
    post_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Not the leading column of the unique constraint, so it keeps its own
    # index for "all likes by this user" lookups.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relationships 
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="likes",
        foreign_keys=[post_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="post_likes",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_like_post_user"),
    )

    def __repr__(self) -> str:
        return f"<PostLike post={self.post_id} user={self.user_id}>"


class PostComment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """parent_comment_id null = top-level comment; filled = reply to
    another comment (self-referential FK)."""

    __tablename__ = "post_comments"

    # No separate index=True on post_id — covered by the (post_id,
    # created_at) composite index below.
    post_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Self-referential — null = top-level comment, set = reply
    parent_comment_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("post_comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="comments",
        foreign_keys=[post_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="post_comments",
        foreign_keys=[user_id],
    )

    parent_comment: Mapped["PostComment"] = relationship(
        "PostComment",
        back_populates="replies",
        remote_side="PostComment.id",
        foreign_keys=[parent_comment_id],
    )

    replies: Mapped[list["PostComment"]] = relationship(
        "PostComment",
        back_populates="parent_comment",
        foreign_keys=[parent_comment_id],
    )

    __table_args__ = (
        Index("ix_post_comments_post_created_at", "post_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<PostComment id={self.id} post={self.post_id} user={self.user_id}>"


class PostShare(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "post_shares"

    # Single-column indexes — neither is the leading column of a composite
    # here, so both keep index=True.
    post_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="shares",
        foreign_keys=[post_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="post_shares",
        foreign_keys=[user_id],
    )

    def __repr__(self) -> str:
        return f"<PostShare post={self.post_id} user={self.user_id}>"


class PostSave(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "post_saves"

    post_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="saves",
        foreign_keys=[post_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="post_saves",
        foreign_keys=[user_id],
    )

    def __repr__(self) -> str:
        return f"<PostSave post={self.post_id} user={self.user_id}>"