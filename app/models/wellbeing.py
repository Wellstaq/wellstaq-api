"""
Wellbeing models — dimensions, challenges, and scores.

Design principles:
- WellbeingDimension: lookup table for measurable categories
  (e.g. "Physical", "Mental", "Financial"). Minimal by design.
- WellbeingChallenge: named wellbeing challenge/program. Minimal by
  design per current schema — no org scope yet, expand later if needed.
- WellbeingScore: one row per (user, dimension, period). period is a
  free string — format (weekly/monthly/etc.) decided at app layer.
  score is numeric to support decimal precision (e.g. 7.5 out of 10).
  Composite index on (user_id, period) supports the common query:
  "give me all of this user's dimension scores for this period."
  ondelete=CASCADE on both FKs — scores are meaningless without the
  user or the dimension they belong to.
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

"""
WellbeingChallenge model — a category/theme that challenges can belong to.

Design principles:
- Simple lookup/category table (e.g. "Fitness", "Mental Health", "Nutrition").
- A WellbeingChallenge can have many Challenge instances under it.
"""


class WellbeingChallenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wellbeing_challenges"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge",
        back_populates="wellbeing_challenge",
        # No delete cascade — Challenge.wellbeing_challenge_id is nullable
        # with ondelete=SET NULL; challenges survive category deletion.
    )

    organizations: Mapped[list["OrganizationWellbeingChallenge"]] = relationship(
        "OrganizationWellbeingChallenge",
        back_populates="wellbeing_challenge",
        cascade="all, delete-orphan",
    )
    def __repr__(self) -> str:
        return f"<WellbeingChallenge id={self.id} name={self.name}>"

class WellbeingScore(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "wellbeing_scores"

    # ── FKs ───────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wellbeing_dimensions.id", ondelete="CASCADE"),  # ← fixed
        nullable=False,
    )
    # ── Score data ────────────────────────────────────────────────────────
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    # Free string — e.g. "2026-06", "2026-W25" — format decided at app layer
    period: Mapped[str] = mapped_column(String(20), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="wellbeing_scores",
    )
    dimension: Mapped["WellbeingDimension"] = relationship(
        "WellbeingDimension",
        back_populates="scores",
    )

    __table_args__ = (
        # Common query pattern: all of a user's dimension scores for a given period
        Index("ix_wellbeing_scores_user_period", "user_id", "period"),
    )

    def __repr__(self) -> str:
        return f"<WellbeingScore user_id={self.user_id} dimension_id={self.dimension_id} period={self.period} score={self.score}>"

"""
OrganizationWellbeingChallenge model — links an organization to the
wellbeing categories/themes it has opted into.

Design principles:
- Pure join table: organization_id + wellbeing_challenge_id, no extra
  columns beyond the PK, matching the DBML schema.
- This is distinct from Organization.challenges (org -> Challenge
  instances) and from per-user challenge participation — this table is
  company-level configuration: "which WellbeingChallenge categories is
  this org opted into", not which user is doing what.
- ondelete=CASCADE on both FKs — the link row is meaningless without
  either parent, same pattern as WellbeingScore and OrganizationInvite.
- Unique constraint on (organization_id, wellbeing_challenge_id) — an
  org shouldn't be able to opt into the same category twice.
- created_at / updated_at come from TimestampMixin.
"""



class OrganizationWellbeingChallenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
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


#  UserWellbeingBaseline

# Design principles:
# - Captured once at signup (per dimension) — a snapshot of where the user
#   started, not an ongoing log. dimension + level + reason are all free
#   strings validated at the app layer (matches Organization.organization_type
#   / work_model pattern already used elsewhere in this codebase).
# - dimension: mood | stress | energy | work_life_balance
# - level: good | bad | stressed | tired
# - reason: work | family | health | finances | etc — free text category
# - recorded_at is a plain DateTime, not TimestampMixin's created_at: the
#   baseline may be recorded at a different moment than the row is inserted
#   (e.g. backfilled from an onboarding form submitted earlier), so they're
#   kept as distinct concepts. created_at/updated_at still come from
#   TimestampMixin for row audit purposes.
# - ondelete=CASCADE on user_id — a baseline snapshot is meaningless without
#   the user it describes.


class UserWellbeingBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_wellbeing_baselines"

    # ── FK ────────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Baseline data ─────────────────────────────────────────────────────
    # Free strings — validated at app/schema layer (no DB enum constraint)
    dimension: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="wellbeing_baselines",
    )

    def __repr__(self) -> str:
        return (
            f"<UserWellbeingBaseline user_id={self.user_id} "
            f"dimension={self.dimension} level={self.level}>"
        )