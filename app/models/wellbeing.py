"""
Wellbeing models — dimensions, challenges, scores, baselines, and health
assessments.

organization_id is present on all non-global tables (WellbeingScore,
UserWellbeingBaseline, UserHealthAssessment). Global/shared lookup tables
(WellbeingDimension, WellbeingChallenge) remain unscoped — every org uses
the same taxonomy.

Design principles:
- WellbeingDimension: global lookup table for measurable categories
  (e.g. "Physical", "Mental", "Financial"). NOT org-scoped.
- WellbeingChallenge: global lookup table for challenge categories.
  NOT org-scoped. Org-specific opt-in is OrganizationWellbeingChallenge.
- WellbeingScore: one row per (user, dimension, period). organization_id
  added for direct org-level querying and tenant isolation. Composite
  index on (organization_id, user_id, period) covers the common query
  patterns. ondelete=CASCADE on all FKs.
- UserWellbeingBaseline: captured once at signup per dimension.
  organization_id added for tenant isolation.
- UserHealthAssessment: health risk assessment result per user.
  organization_id added for tenant isolation.
- OrganizationWellbeingChallenge: company-level config — unchanged.
"""

from sqlalchemy import ForeignKey, Index, Numeric, String, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class WellbeingDimension(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "wellbeing_dimensions"

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    scores: Mapped[list["WellbeingScore"]] = relationship(
        "WellbeingScore",
        back_populates="dimension",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<WellbeingDimension id={self.id} name={self.name}>"


class WellbeingChallenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A category/theme that Challenge instances can belong to — e.g.
    "Fitness", "Mental Health", "Nutrition". Global lookup table, not
    org-scoped; org-specific opt-in is OrganizationWellbeingChallenge."""

    __tablename__ = "wellbeing_challenges"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge",
        back_populates="wellbeing_challenge",
    )

    organizations: Mapped[list["OrganizationWellbeingChallenge"]] = relationship(
        "OrganizationWellbeingChallenge",
        back_populates="wellbeing_challenge",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<WellbeingChallenge id={self.id} name={self.name}>"


class WellbeingScore(Base, UUIDPrimaryKeyMixin):
    """One row per (user, dimension, period).

    organization_id is present for direct tenant isolation and org-level
    querying without joining through User.
    """

    __tablename__ = "wellbeing_scores"

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
    dimension_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wellbeing_dimensions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Score data ────────────────────────────────────────────────────────
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="wellbeing_scores",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="wellbeing_scores",
        foreign_keys=[user_id],
    )
    dimension: Mapped["WellbeingDimension"] = relationship(
        "WellbeingDimension",
        back_populates="scores",
        foreign_keys=[dimension_id],
    )

    __table_args__ = (
        Index("ix_wellbeing_scores_org_user_period", "organization_id", "user_id", "period"),
    )

    def __repr__(self) -> str:
        return (
            f"<WellbeingScore org={self.organization_id} user_id={self.user_id} "
            f"dimension_id={self.dimension_id} period={self.period} score={self.score}>"
        )


class OrganizationWellbeingChallenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Links an organization to the wellbeing categories/themes it has
    opted into. Company-level configuration — organization_id belongs here.

    - Pure join table: organization_id + wellbeing_challenge_id.
    - ondelete=CASCADE on both FKs.
    - Unique constraint on (organization_id, wellbeing_challenge_id).
    """

    __tablename__ = "organization_wellbeing_challenges"

    # ── FKs ───────────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wellbeing_challenge_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wellbeing_challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="wellbeing_challenges",
        foreign_keys=[organization_id],
    )
    wellbeing_challenge: Mapped["WellbeingChallenge"] = relationship(
        "WellbeingChallenge",
        back_populates="organizations",
        foreign_keys=[wellbeing_challenge_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "wellbeing_challenge_id",
            name="uq_org_wellbeing_challenge",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<OrganizationWellbeingChallenge org={self.organization_id} "
            f"challenge={self.wellbeing_challenge_id}>"
        )


class UserWellbeingBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Captured once at signup (per dimension) — a snapshot of where the
    user started.

    organization_id is present for tenant isolation and direct org-level
    querying without joining through User.

    - dimension: mood | stress | energy | work_life_balance
    - level: good | bad | stressed | tired
    - reason: free text category — work | family | health | finances | etc
    - recorded_at is separate from TimestampMixin's created_at — the
      baseline may be recorded at a different moment than row insertion.
    - ondelete=CASCADE on user_id and organization_id.
    """

    __tablename__ = "user_wellbeing_baselines"

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

    # ── Baseline data ─────────────────────────────────────────────────────
    dimension: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="wellbeing_baselines",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="wellbeing_baselines",
        foreign_keys=[user_id],
    )

    def __repr__(self) -> str:
        return (
            f"<UserWellbeingBaseline org={self.organization_id} user_id={self.user_id} "
            f"dimension={self.dimension} level={self.level}>"
        )


class UserHealthAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A user's health risk assessment result (e.g. diabetes risk, kidney
    disease risk).

    organization_id is present for tenant isolation and direct org-level
    querying.

    - category_id is a FK to health_categories.id.
    - risk_score is numeric for decimal precision.
    - status: free string — "pending" | "completed" | "reviewed" — app validated.
    - assessed_at is separate from created_at — marks when the assessment
      was conducted, which may differ from row creation.
    - ondelete=CASCADE on user_id and organization_id.
    """

    __tablename__ = "user_health_assessments"

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
    category_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("health_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Assessment data ───────────────────────────────────────────────────
    risk_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="health_assessments",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="health_assessments",
        foreign_keys=[user_id],
    )
    category: Mapped["HealthCategory"] = relationship(
        "HealthCategory",
        back_populates="assessments",
        foreign_keys=[category_id],
    )

    def __repr__(self) -> str:
        return (
            f"<UserHealthAssessment org={self.organization_id} "
            f"user_id={self.user_id} status={self.status}>"
        )