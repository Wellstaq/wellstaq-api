"""
LeaderboardSnapshot, Target, and KPISnapshot models.

Design principles:
- LeaderboardSnapshot is computed by a backend scheduled job — never
  written directly by users. previous_rank / previous_org_rank drive
  the up/down arrow UI.
- Target is branch-level, managed by Branch/Super Admin. One target
  per (branch_id, metric_type, period) combination. Org-wide targets
  are NOT stored as separate rows — they're a derived aggregate
  (e.g. SUM over all branches in the org) computed at read time or by
  a rollup job, since there's no ranking ambiguity to resolve for a
  target the way there is for a leaderboard rank.
- KPISnapshot is branch-level, computed by backend. One snapshot per
  (branch_id, metric_name, period_start) combination. Same rollup
  story as Target for the org-wide figure.

Tenant + branch scoping:
- Every user belongs to exactly one branch (users.branch_id is
  NOT NULL), so branch_id is denormalized onto all three tables here
  as NOT NULL too — there is no "unscoped" leaderboard/target/KPI row.
- organization_id is KEPT alongside branch_id (rather than derived via
  a join to branches) so that org-wide queries and rollups don't need
  to join through branches, and so ON DELETE CASCADE from Organization
  still works even if branch cascade ordering ever changes.

Org-wide leaderboard algorithm (background job):
  For each organization, for each metric_type/period being recomputed:
    1. Pull every user's raw metric value for that org + period,
       across ALL of the org's branches (this requires reading the
       underlying activity data, not previously-computed ranks —
       ranks from different branches are not comparable, only the
       raw values are).
    2. Sort the full org-wide set by value -> assign org_rank.
    3. Group the same data by branch_id, sort within each group by
       value -> assign rank (the branch-local rank).
    4. Bulk upsert one LeaderboardSnapshot row per user carrying both
       rank (branch scope) and org_rank (org scope), plus
       previous_rank / previous_org_rank pulled from the prior run.
  Doing both scopes in the same pass avoids a second read of the
  underlying activity data and guarantees the two numbers are always
  computed from the same snapshot of the underlying metric values.

Unique constraints:
- targets: (branch_id, metric_type, period) — one target per metric
  per period per branch.
- kpi_snapshots: (branch_id, metric_name, period_start) — one
  snapshot per metric per period per branch.
- LeaderboardSnapshot has no uniqueness constraint — the backend job
  can produce multiple snapshots for the same period (e.g. daily +
  weekly), and rank ties are allowed.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LeaderboardSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A point-in-time leaderboard entry computed by the backend scheduler.

    Each row captures one user's rank for one metric_type over one
    period, at TWO scopes simultaneously:
      - rank / previous_rank: the user's position among their own
        branch only.
      - org_rank / previous_org_rank: the user's position across the
        whole organization (all branches combined).
    """

    __tablename__ = "leaderboard_snapshots"

    # ── Tenant + branch scoping ──────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    branch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── User ──────────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Metric ────────────────────────────────────────────────────────────────
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)

    # ── Branch-scoped ranking ─────────────────────────────────────────────────
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nullable — null on the very first computation run when there is no
    # prior rank to compare against.
    previous_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Org-wide ranking (computed across all branches together) ─────────────
    org_rank: Mapped[int] = mapped_column(Integer, nullable=False)

    previous_org_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Period ────────────────────────────────────────────────────────────────
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)  # daily | weekly | monthly

    # ── Computed timestamp ────────────────────────────────────────────────────
    computed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="leaderboard_snapshots",
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="leaderboard_snapshots",
        foreign_keys=[branch_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="leaderboard_snapshots",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        Index(
            "ix_leaderboard_snapshots_branch_metric_period_rank",
            "organization_id",
            "branch_id",
            "metric_type",
            "period_start",
            "rank",
        ),
        Index(
            "ix_leaderboard_snapshots_org_metric_period_org_rank",
            "organization_id",
            "metric_type",
            "period_start",
            "org_rank",
        ),
        CheckConstraint("rank > 0", name="ck_leaderboard_snapshots_rank_positive"),
        CheckConstraint(
            "previous_rank IS NULL OR previous_rank > 0",
            name="ck_leaderboard_snapshots_previous_rank_positive",
        ),
        CheckConstraint(
            "org_rank > 0", name="ck_leaderboard_snapshots_org_rank_positive"
        ),
        CheckConstraint(
            "previous_org_rank IS NULL OR previous_org_rank > 0",
            name="ck_leaderboard_snapshots_previous_org_rank_positive",
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_leaderboard_snapshots_period_range",
        ),
        CheckConstraint("period_type IN ('daily', 'weekly', 'monthly')", name="ck_leaderboard_snapshots_period_type_valid"),
        UniqueConstraint(
            "organization_id", "user_id", "metric_type", "period_type", "period_start", "period_end",
            name="uq_leaderboard_snapshots_org_user_metric_period",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<LeaderboardSnapshot id={self.id} org={self.organization_id} "
            f"branch={self.branch_id} user={self.user_id} "
            f"metric={self.metric_type} rank={self.rank} org_rank={self.org_rank}>"
        )


class Target(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A branch-level KPI target set by Branch/Super Admin.

    One target per (branch_id, metric_type, period) — the unique
    constraint enforces this at the DB level. The service layer should
    upsert rather than blindly insert to avoid duplicate-key errors.

    An org-wide target figure (e.g. "what's the whole org aiming for
    this week") is a derived aggregate over all of an org's branch
    targets — it is intentionally NOT stored as its own row here.
    """

    __tablename__ = "targets"

    # ── Tenant + branch scoping ──────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    branch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Target definition ─────────────────────────────────────────────────────
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # period is a free-form label: "2026-W23", "2026-06", "2026-Q2", etc.
    period: Mapped[str] = mapped_column(String(50), nullable=False)

    target_value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="targets",
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="targets",
        foreign_keys=[branch_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "branch_id",
            "metric_type",
            "period",
            name="uq_targets_branch_metric_period",
        ),
        Index(
            "ix_targets_branch_metric_period",
            "branch_id",
            "metric_type",
            "period",
        ),
        # Non-unique — used to roll targets up to an org-wide figure
        # (e.g. SUM(target_value) GROUP BY organization_id) without a
        # join through branches.
        Index(
            "ix_targets_org_metric_period",
            "organization_id",
            "metric_type",
            "period",
        ),
        CheckConstraint(
            "length(trim(metric_type)) > 0",
            name="ck_targets_metric_type_not_empty",
        ),
        CheckConstraint(
            "length(trim(period)) > 0",
            name="ck_targets_period_not_empty",
        ),
        CheckConstraint(
            "target_value > 0",
            name="ck_targets_target_value_positive",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Target id={self.id} org={self.organization_id} "
            f"branch={self.branch_id} metric={self.metric_type} "
            f"period={self.period} target_value={self.target_value}>"
        )


class KPISnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A computed branch-level KPI data point written by the backend
    scheduler.

    One snapshot per (branch_id, metric_name, period_start) — the
    unique constraint prevents duplicate writes for the same period.
    Org-wide KPI figures are a derived aggregate over all of an org's
    branch snapshots, not a stored row here.
    """

    __tablename__ = "kpi_snapshots"

    # ── Tenant + branch scoping ──────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    branch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── KPI definition ────────────────────────────────────────────────────────
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="kpi_snapshots",
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="kpi_snapshots",
        foreign_keys=[branch_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "branch_id",
            "metric_name",
            "period_start",
            name="uq_kpi_snapshots_branch_metric_period",
        ),
        Index(
            "ix_kpi_snapshots_branch_metric_period_start",
            "branch_id",
            "metric_name",
            "period_start",
        ),
        # Non-unique — used for org-wide rollups (e.g. AVG(value)
        # GROUP BY organization_id) without a join through branches.
        Index(
            "ix_kpi_snapshots_org_metric_period_start",
            "organization_id",
            "metric_name",
            "period_start",
        ),
        CheckConstraint(
            "length(trim(metric_name)) > 0",
            name="ck_kpi_snapshots_metric_name_not_empty",
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_kpi_snapshots_period_range",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<KPISnapshot id={self.id} org={self.organization_id} "
            f"branch={self.branch_id} metric={self.metric_name} "
            f"period_start={self.period_start} value={self.value}>"
        )