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

Tenant isolation (ChallengeParticipant):
- organization_id is denormalized directly onto ChallengeParticipant —
  not just derivable through challenge_id. Same reasoning as Post's
  child tables (PostLike, PostComment, PostShare, PostSave, see
  post.py): user_id and challenge_id are independent FKs, and a DB
  constraint cannot compare values across two different tables' rows.
  organization_id here is set as a COPY of the parent Challenge's
  organization_id at insert time — never independently chosen.
  
Enterprise Multi-Tenant Hardening:
- Rather than trusting the service layer exclusively, consistency is enforced 
  at the database layer using a composite Foreign Key Constraint on 
  (challenge_id, organization_id) matching Challenges(id, organization_id).
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Numeric,
    Text,
    UniqueConstraint,
    func,
   
)
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
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # ── Schedule / status ────────────────────────────────────────────────────
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="upcoming")
    metric_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="cumulative")
    # "cumulative" = sum across the whole challenge window, deadline-driven
    # "daily_minimum" = must independently clear target_value each day

    target_value: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    # The number to hit — total steps for cumulative, per-day minimum for daily_minimum

    # ── Tracking method ──────────────────────────────────────────────────────
    # "automatic" = fed from a connected health/fitness source (see
    #   AUTOMATIC_ACTIVITY_METRICS / sync_health_activity) — manual progress
    #   submission is rejected for these.
    # "self_reported" = participant logs their own value/completion via
    #   POST .../progress — no evidence required.
    # "evidence_based" = participant submits via POST .../submissions
    #   (photo/note); counts toward progress only once review_status is
    #   'approved' — immediately if requires_approval is False (user
    #   declaration), otherwise after an admin/moderator reviews it.
    # "platform_recorded" = Wellstaq records participation itself because it
    #   happened inside the platform (e.g. event attendance) — no
    #   participant-submitted progress at all.
    tracking_method: Mapped[str] = mapped_column(String(20), nullable=False, default="self_reported")

    # Human-readable definition of the specific activity being tracked, e.g.
    # "Prepare a healthy meal containing vegetables." Shown to participants
    # instead of letting them type an arbitrary value for what counts.
    activity_label: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Only meaningful for tracking_method='evidence_based'.
    evidence_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Whether a submitted entry needs admin/moderator approval before it
    # counts toward progress, or counts immediately on submission
    # ("user declaration" per spec).
    requires_approval: Mapped[bool] = mapped_column(nullable=False, default=False)

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
        primaryjoin="Challenge.id == ChallengeParticipant.challenge_id",
    )

    progress_entries: Mapped[list["ChallengeProgressEntry"]] = relationship(
        "ChallengeProgressEntry",
        back_populates="challenge",
        cascade="all, delete-orphan",
        primaryjoin="Challenge.id == ChallengeProgressEntry.challenge_id",
    )

    __table_args__ = (
        Index("ix_challenges_org_start_date", "organization_id", "start_date"),
        UniqueConstraint("id", "organization_id", name="uq_challenges_id_org"),
        CheckConstraint("length(trim(name)) > 0", name="ck_challenges_name_not_empty"),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_challenges_date_range",
        ),
        CheckConstraint(
            "status IN ('upcoming', 'active', 'completed', 'cancelled', 'archived')",
            name="ck_challenges_status_valid",
        ),
        CheckConstraint("target_type IN ('cumulative', 'daily_minimum')", name="ck_challenges_target_type_valid"),
        CheckConstraint(
            "tracking_method IN ('automatic', 'self_reported', 'evidence_based', 'platform_recorded')",
            name="ck_challenges_tracking_method_valid",
        ),
    )

    def __repr__(self) -> str:
        return f"<Challenge id={self.id} name={self.name} org={self.organization_id}>"


class ChallengeParticipant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "challenge_participants"

    # ── Challenge / User ──────────────────────────────────────────────────
    challenge_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalized — composite constraints ensure this matches the parent 
    # challenge's organization tracking id perfectly.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
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

    # Admin/moderator-assigned quality score (0-100) for challenges judged
    # on submission quality rather than plain completion count — see
    # set_participant_score. Null until an admin scores this participant.
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    challenge: Mapped["Challenge"] = relationship(
        "Challenge",
        back_populates="participants",
        primaryjoin="ChallengeParticipant.challenge_id == Challenge.id",
        foreign_keys=[challenge_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="challenge_participations",
        foreign_keys=[user_id],
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_challenge_participant_challenge_user"),
        ForeignKeyConstraint(
            ["challenge_id", "organization_id"],
            ["challenges.id", "challenges.organization_id"],
            ondelete="CASCADE",
            name="fk_challenge_participants_challenge_org",
        ),
        CheckConstraint("rank IS NULL OR rank > 0", name="ck_challenge_participants_rank_positive"),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)", name="ck_challenge_participants_score_range",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ChallengeParticipant id={self.id} challenge={self.challenge_id} "
            f"user={self.user_id} org={self.organization_id}>"
        )
"""

Raw daily progress data submitted by the frontend (device pedometer via
HealthKit/Health Connect, or manual entry). Challenge.target_type decides
how these are evaluated: cumulative = SUM across all entries vs
target_value; daily_minimum = COUNT of entries where value >=
target_value.

entry_date is the WAT (Africa/Lagos) calendar date the entry belongs to
— computed by the backend from server time at insert, never trusted from
the client. This keeps "which day is this" consistent across devices/
timezones rather than trusting a client-reported date.
"""

class ChallengeProgressEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "challenge_progress_entries"

    challenge_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Where this number came from — useful for audits/disputes even
    # though only one source is supported today.
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")

    # ── Evidence-based submissions ───────────────────────────────────────────
    # Populated only when the parent Challenge.tracking_method is
    # 'evidence_based'; null for automatic/self_reported entries.
    photo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 'approved' by default so automatic/self_reported entries (and
    # evidence_based entries on a challenge with requires_approval=False)
    # count toward progress immediately. Only an evidence_based entry on a
    # requires_approval challenge starts 'pending'. check_challenge_completion
    # and get_participant_progress only count 'approved' entries.
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="approved")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    challenge: Mapped["Challenge"] = relationship(
        "Challenge", back_populates="progress_entries", foreign_keys=[challenge_id],
    )
    user: Mapped["User"] = relationship(
        "User", back_populates="challenge_progress_entries", foreign_keys=[user_id],
    )
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="challenge_progress_entries", foreign_keys=[organization_id],
    )

    __table_args__ = (
        # One entry per person per day per challenge — a re-sync for the
        # same day UPDATES this row, never duplicates it.
        UniqueConstraint("challenge_id", "user_id", "entry_date", name="uq_challenge_progress_entry_day"),
        ForeignKeyConstraint(
            ["challenge_id", "organization_id"],
            ["challenges.id", "challenges.organization_id"],
            ondelete="CASCADE",
            name="fk_challenge_progress_challenge_org",
        ),
        CheckConstraint("value >= 0", name="ck_challenge_progress_value_non_negative"),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')", name="ck_challenge_progress_review_status_valid",
        ),
    )

    def __repr__(self) -> str:
        return f"<ChallengeProgressEntry challenge={self.challenge_id} user={self.user_id} date={self.entry_date} value={self.value}>"

class ChallengeStatsSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One row per (organization_id, branch_id, snapshot_date). branch_id is
    nullable — null row = org-wide overview snapshot (scope='all'), a
    non-null row = that branch's own-members snapshot (matching the same
    branch-membership rule used in get_challenge_stats).

    Written once daily by compute_challenge_stats_snapshots. Trend
    comparisons look up the snapshot closest to N days ago; if none
    exists yet (e.g. first week after this feature ships), trend is
    omitted rather than guessed.
    """
    __tablename__ = "challenge_stats_snapshots"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    branch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("branches.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    active_challenges: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_participants: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_participants: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_challenges: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upcoming_challenges: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("organization_id", "branch_id", "snapshot_date", name="uq_challenge_stats_snapshot_day"),
    )