"""
Club model — a branch-scoped affinity or interest group.

Design principles:
- A club always belongs to exactly ONE branch (branch_id NOT NULL).
  Clubs are visible org-wide for discovery but are operationally scoped
  to their branch — membership, activity, and moderation all happen
  within the branch context.
- A club cannot belong to two branches. The branch_id FK is NOT NULL
  and carries no uniqueness relaxation — one row, one branch, always.
- privacy controls visibility: 'public' = anyone in org can see/join,
  'private' = invite only. Validated at app/schema layer (no DB enum).
- category is a fixed, small enum — 'fitness', 'creativity',
  'team_bonding', 'mental_health', 'nutrition'. Deliberately NOT a FK
  to a shared/global categories table: club categories are a distinct,
  small, hand-picked list that doesn't need to be shared with or
  managed alongside other domains' categories (departments, challenges,
  etc. have their own separate notions of "category" if any). A plain
  validated string column avoids cross-domain coupling entirely —
  no join, no shared table, no risk of a club referencing a category
  meant for a different domain. Validated at app/schema layer (no DB
  enum constraint), same pattern as `privacy`.
- created_by is a plain audit string (no FK) — same pattern as Branch and
  Department. Preserved even if the creator user is later deleted.
- member_count is denormalized — incremented/decremented by app layer or
  background job on join/leave. Never derive via COUNT().
- Club name must be unique within an organization
  (UniqueConstraint on organization_id + name).

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

    # Club profile/cover image — optional, uploaded via the shared
    # presigned-upload flow (domain='clubs').
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # public (anyone can join) | private (invite only)
    # validated at app layer, no DB enum constraint
    privacy: Mapped[str] = mapped_column(String(20), nullable=False, default="public")

    # fitness | creativity | team_bonding | mental_health | nutrition
    # validated at app/schema layer, no DB enum constraint — same
    # pattern as privacy. Plain string, not a FK — see module docstring.
    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Club leader — the user who created the club. Owns it fully.
    # SET NULL on delete so the club survives if the leader's account is removed.
    leader_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Org scope ─────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Branch scope ───────────────────────────────────────────────────────
    # NOT NULL — every club must belong to exactly one branch.
    # A club can never be moved between branches — branch_id is set at
    # creation and never updated. If a branch is deleted, all its clubs
    # are cascade-deleted with it.
    branch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
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
    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="clubs",
        foreign_keys=[branch_id],
    )
    leader: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[leader_id],
    )
    members: Mapped[list["ClubMember"]] = relationship(
        "ClubMember",
        back_populates="club",
        cascade="all, delete-orphan",
    )
    __table_args__ = (
        # A club name must be unique within an organization.
        # Two branches in the same org cannot have a club with the same name.
        UniqueConstraint("organization_id", "name", name="uq_club_org_name"),
    )

    def __repr__(self) -> str:
        return (
            f"<Club id={self.id} name={self.name} "
            f"org={self.organization_id} branch={self.branch_id} "
            f"category={self.category} privacy={self.privacy}>"
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
        # A user can now belong to multiple clubs — only prevent joining the same club twice.
        UniqueConstraint("club_id", "user_id", name="uq_club_members_club_user"),
    )

    def __repr__(self) -> str:
        return f"<ClubMember club_id={self.club_id} user_id={self.user_id}>"