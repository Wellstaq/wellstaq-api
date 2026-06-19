"""
Club model — an org-wide affinity or interest group.

Design principles:
- Clubs are created only by overall_manager and are intentionally NOT
  branch-scoped — they are visible across the entire organization.
- privacy controls visibility: 'public' = anyone in org can see/join,
  'private' = invite only. Validated at app/schema layer (no DB enum).
- category_id is a plain string stub — FK added when categories table exists.
- created_by is a plain audit string (no FK) — same pattern as Branch and
  Department. Preserved even if the creator user is later deleted.
- member_count is denormalized — incremented/decremented by app layer or
  background job on join/leave. Never derive via COUNT().
- No updated_at — clubs are mostly append-only; name/description edits
  are infrequent. Add if needed later.
"""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from datetime import datetime, timezone



class Club(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clubs"

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # public | private — validated at app layer, no DB enum constraint
    privacy: Mapped[str] = mapped_column(String(20), nullable=False, default="public")

    # ── Category (FK added when categories table exists) ──────────────────
    #category_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Org scope ─────────────────────────────────────────────────────────
    # Clubs are org-wide — intentionally no branch_id
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    # Plain string — no FK, audit trail preserved even if creator is deleted
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Denormalized counter ───────────────────────────────────────────────
    # Managed by app layer / background job — never use COUNT() in queries
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="clubs",
        foreign_keys=[organization_id],
    )

    """ ── Category relationship (added when categories table exists) ──────
    category = relationship("Category", back_populates="clubs")
    """
    members: Mapped[list["ClubMember"]] = relationship(
        "ClubMember",
        back_populates="club",
        cascade="all, delete-orphan",
    )
    posts: Mapped[list["Post"]] = relationship(
        "Post",
        back_populates="club",
    )

    def __repr__(self) -> str:
        return f"<Club id={self.id} name={self.name} org={self.organization_id} privacy={self.privacy}>"


"""
ClubMember model — junction table between clubs and users.

Design principles:
- Pure many-to-many join: one row per user per club.
- (club_id, user_id) unique constraint prevents duplicate membership.
- Leaving a club deletes the row — no soft delete here.
- joined_at is set automatically at the app layer when the row is created.
- No role column for now — add later if club admin roles are needed.
"""


class ClubMember(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "club_members"

    # ── FKs ───────────────────────────────────────────────────────────────
    club_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("clubs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Membership timestamp ───────────────────────────────────────────────
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    club: Mapped["Club"] = relationship(
        "Club",
        back_populates="members",
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="club_memberships",
    )

    __table_args__ = (
        # One membership row per user per club
        UniqueConstraint("club_id", "user_id", name="uq_club_members_club_user"),
    )

    def __repr__(self) -> str:
        return f"<ClubMember club_id={self.club_id} user_id={self.user_id}>"