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

ClubMember design:
- A user can only belong to ONE club at a time.
  UniqueConstraint("user_id") enforces this at the DB level — attempting
  to insert a second ClubMember row for the same user raises IntegrityError.
- Changing clubs = DELETE the existing ClubMember row, INSERT a new one.
  The service layer must do this atomically in one transaction.
- (club_id, user_id) unique constraint is kept alongside the user_id
  constraint — it covers the "same user re-joining the same club" case
  which the user_id constraint alone would also catch, but the named
  constraint makes the violation message clearer.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Club(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clubs"

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # public (anyone can join) | private (invite only)
    # validated at app layer, no DB enum constraint
    privacy: Mapped[str] = mapped_column(String(20), nullable=False, default="public")

    category_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

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
    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="clubs",
        foreign_keys=[category_id],
    )
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
        return (
            f"<Club id={self.id} name={self.name} "
            f"org={self.organization_id} privacy={self.privacy}>"
        )


class ClubMember(Base, UUIDPrimaryKeyMixin):
    """
    Junction table between clubs and users.

    One user → one club at a time.
    UniqueConstraint("user_id") is the DB-level enforcement.

    To move a user to a different club:
        await db.delete(existing_membership)
        await db.flush()
        db.add(ClubMember(club_id=new_club_id, user_id=user_id))
        await db.flush()

    Both operations must happen in the same transaction so the user is
    never left without a club mid-request.
    """

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
        # One user → one club at a time.
        # This is the primary business rule constraint.
        UniqueConstraint("user_id", name="uq_club_members_one_club_per_user"),
        # Redundant given the above, but kept for a clearer error message
        # on the specific "re-joining the same club" case.
        UniqueConstraint("club_id", "user_id", name="uq_club_members_club_user"),
    )

    def __repr__(self) -> str:
        return f"<ClubMember club_id={self.club_id} user_id={self.user_id}>"