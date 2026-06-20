"""
Challenge model — an organization- or branch-level wellbeing challenge.

Design principles:
- A challenge belongs to exactly one organization.
- branch_id is nullable: null means the challenge sits at the org level
  rather than under a specific branch.
- created_by references the User who created this challenge (audit trail).
- ChallengeParticipant is the join model tracking a user's participation
  in a challenge — a user may only join a given challenge once
  (unique on challenge_id + user_id).
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Challenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "challenges"

    # ── Organization / Branch scoping ───────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable — null means this challenge sits at the org level, not under
    # a specific branch
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Category / theme ─────────────────────────────────────────────────────
    wellbeing_challenge_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("wellbeing_challenges.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Schedule / status ────────────────────────────────────────────────────
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    metric_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Audit ─────────────────────────────────────────────────────────────
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="challenges",
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="challenges",
        foreign_keys=[branch_id],
    )

    wellbeing_challenge: Mapped["WellbeingChallenge"] = relationship(
        "WellbeingChallenge",
        back_populates="challenges",
        foreign_keys=[wellbeing_challenge_id],
    )

    creator: Mapped["User"] = relationship(
        "User",
        back_populates="created_challenges",
        foreign_keys=[created_by],
    )

    participants: Mapped[list["ChallengeParticipant"]] = relationship(
        "ChallengeParticipant",
        back_populates="challenge",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_challenges_org_start_date", "organization_id", "start_date"),
    )

    def __repr__(self) -> str:
        return f"<Challenge id={self.id} name={self.name} org={self.organization_id}>"


class ChallengeParticipant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "challenge_participants"

    # ── Challenge / User ──────────────────────────────────────────────────
    challenge_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Participation state ──────────────────────────────────────────────────
    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    challenge: Mapped["Challenge"] = relationship(
        "Challenge",
        back_populates="participants",
        foreign_keys=[challenge_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="challenge_participations",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_challenge_participant_challenge_user"),
    )

    def __repr__(self) -> str:
        return f"<ChallengeParticipant id={self.id} challenge={self.challenge_id} user={self.user_id}>"

