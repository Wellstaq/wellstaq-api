"""
ActivityLog and EngagementLog — organization_id added to both for
tenant isolation and direct org-level querying without joining through User.

ActivityLog design principles:
- One row per (user, metric_type, date) is the intended grain, but NOT
  enforced as a DB-level unique constraint — a user can plausibly log
  more than one manual entry or correction for the same metric on the
  same day, and the service layer decides whether to upsert or append.
- Composite index on (organization_id, user_id, date) covers the common
  query pattern: pulling a user's metrics for a single day, or scanning
  a date range for the Employee Engagement chart and weekly/monthly
  filters.
- metric_type and unit are free strings (app-layer validated).
- value is Numeric to handle steps, km distances, reps, minutes without
  lossy float rounding.
- unit is nullable — steps has no unit.
- ondelete=CASCADE on user_id and organization_id.
- No TimestampMixin — `date` is the column this table is queried and
  grouped by. No created_at/updated_at defined in the DBML.

EngagementLog design principles:
- Same shape as ActivityLog but scores are written by a backend job on
  a schedule rather than submitted directly by the user.
- metric_type vocabulary (participation | checkins | social |
  productivity) is free-string / app-layer validated.
- Composite index on (organization_id, user_id, date) for the same
  date-range scan pattern as ActivityLog.
"""

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin


class ActivityLog(Base, UUIDPrimaryKeyMixin):
    """
    One row per logged activity metric for a user on a given day.

    metric_type: steps | distance | squat | run | 7min_workout | manual
    unit: steps=NULL | distance=km | squat=reps | run=km | workout=mins

    Powers the Employee Engagement chart. Filterable by day/week/month
    via the `date` column.
    """

    __tablename__ = "activity_logs"

    # ── FKs ───────────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Data ──────────────────────────────────────────────────────────────
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    date: Mapped["date"] = mapped_column(Date, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="activity_logs",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="activity_logs",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        Index("ix_activity_logs_org_user_date", "organization_id", "user_id", "date"),
    )

    def __repr__(self) -> str:
        return (
            f"<ActivityLog org={self.organization_id} user_id={self.user_id} "
            f"metric_type={self.metric_type} value={self.value} date={self.date}>"
        )


class EngagementLog(Base, UUIDPrimaryKeyMixin):
    """
    Backend-computed engagement score for a user on a given day.

    metric_type: participation | checkins | social | productivity

    Written on a schedule by a backend job, not submitted directly by
    the user.
    """

    __tablename__ = "engagement_logs"

    # ── FKs ───────────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Data ──────────────────────────────────────────────────────────────
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    date: Mapped["date"] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="engagement_logs",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="engagement_logs",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        Index("ix_engagement_logs_org_user_date", "organization_id", "user_id", "date"),
    )

    def __repr__(self) -> str:
        return (
            f"<EngagementLog org={self.organization_id} user_id={self.user_id} "
            f"metric_type={self.metric_type} value={self.value} date={self.date}>"
        )