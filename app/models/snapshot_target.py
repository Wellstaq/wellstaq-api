"""
LeaderboardSnapshot, Target, and KPISnapshot models.

Design principles:
- LeaderboardSnapshot is computed by a backend scheduled job — never
  written directly by users. previous_rank drives the up/down arrow UI.
- Target is org-level, managed by Super Admin. One target per
  (organization_id, metric_type, period) combination.
- KPISnapshot is org-level, computed by backend. One snapshot per
  (organization_id, metric_name, period_start) combination.

Tenant isolation:
- organization_id is present on all three tables and is NOT NULL.
  This ensures every row is always scoped to a specific org without
  requiring a join, matching the pattern used across ActivityLog,
  EngagementLog, ChallengeParticipant, etc.

Unique constraints:
- targets: (organization_id, metric_type, period) — one target per
  metric per period per org.
- kpi_snapshots: (organization_id, metric_name, period_start) — one
  snapshot per metric per period per org.
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
    period. previous_rank is the rank from the prior computation run —
    comparing rank vs previous_rank gives the up/down/no-change arrow
    shown in the UI.
    """

    __tablename__ = "leaderboard_snapshots"

    # ── Tenant scoping ────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
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

    # ── Ranking ───────────────────────────────────────────────────────────────
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nullable — null on the very first computation run when there is no
    # prior rank to compare against.
    previous_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Period ────────────────────────────────────────────────────────────────
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

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

    user: Mapped["User"] = relationship(
        "User",
        back_populates="leaderboard_snapshots",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        Index(
            "ix_leaderboard_snapshots_org_metric_period_rank",
            "organization_id",
            "metric_type",
            "period_start",
            "rank",
        ),
        CheckConstraint("rank > 0", name="ck_leaderboard_snapshots_rank_positive"),
        CheckConstraint(
            "previous_rank IS NULL OR previous_rank > 0",
            name="ck_leaderboard_snapshots_previous_rank_positive",
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_leaderboard_snapshots_period_range",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<LeaderboardSnapshot id={self.id} org={self.organization_id} "
            f"user={self.user_id} metric={self.metric_type} rank={self.rank}>"
        )


class Target(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    An org-level KPI target set by Super Admin.

    One target per (organization_id, metric_type, period) — the unique
    constraint enforces this at the DB level. The service layer should
    upsert rather than blindly insert to avoid duplicate-key errors.
    """

    __tablename__ = "targets"

    # ── Tenant scoping ────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
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

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "metric_type",
            "period",
            name="uq_targets_org_metric_period",
        ),
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
            f"metric={self.metric_type} period={self.period} "
            f"target_value={self.target_value}>"
        )


class KPISnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A computed org-level KPI data point written by the backend scheduler.

    One snapshot per (organization_id, metric_name, period_start) —
    the unique constraint prevents duplicate writes for the same period.
    """

    __tablename__ = "kpi_snapshots"

    # ── Tenant scoping ────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
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

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "metric_name",
            "period_start",
            name="uq_kpi_snapshots_org_metric_period",
        ),
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
            f"metric={self.metric_name} period_start={self.period_start} "
            f"value={self.value}>"
        )