"""
app/models/wellbeing_survey.py

Wellbeing Pulse survey system — org-configurable cadence, discrete
survey windows, per-question 1-5 responses, and computed snapshots.

Two distinct aggregations, both stored per window (see
WellbeingPulseSnapshot):
  - Per-question: % of respondents answering 4-5 on THAT question.
  - Per-person combined: each person's 5 answers averaged into one
    score, then bucketed by the same 75/60 thresholds, giving a
    workforce-wide 3-band distribution (Priority support / Needs
    attention / Doing well). This is NOT a re-slice of the per-question
    numbers — it's a separate calculation over each individual's
    combined answers.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PULSE_QUESTIONS = {
    "stress_manageability": "I can manage my current stress effectively.",
    "energy_recovery": "I have enough energy and recovery time.",
    "connection_belonging": "I feel connected and supported at work.",
    "workload_sustainability": "My workload feels sustainable.",
    "workplace_comfort": "My work environment supports my wellbeing.",
}
PULSE_QUESTION_KEYS = tuple(PULSE_QUESTIONS.keys())

SURVEY_CADENCES = ("weekly", "biweekly", "monthly")

STATUS_LOOKING_GOOD = "Looking good"
STATUS_NEEDS_ATTENTION = "Needs attention"
STATUS_PRIORITY_FOCUS = "Priority focus"


def score_to_status(percent: float) -> str:
    """Shared threshold logic — 75+ green, 60-74 amber, <60 red. Used
    identically for per-question status AND per-person banding, per the
    product spec reusing the same thresholds for both."""
    if percent >= 75:
        return STATUS_LOOKING_GOOD
    if percent >= 60:
        return STATUS_NEEDS_ATTENTION
    return STATUS_PRIORITY_FOCUS


class WellbeingSurveyConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wellbeing_survey_configs"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
    )
    cadence: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    organization: Mapped["Organization"] = relationship("Organization", foreign_keys=[organization_id])

    __table_args__ = (
        CheckConstraint("cadence IN ('weekly', 'biweekly', 'monthly')", name="ck_wellbeing_survey_configs_cadence"),
    )

    def __repr__(self) -> str:
        return f"<WellbeingSurveyConfig org={self.organization_id} cadence={self.cadence}>"


class WellbeingSurveyWindow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wellbeing_survey_windows"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    is_closed: Mapped[bool] = mapped_column(nullable=False, default=False)

    organization: Mapped["Organization"] = relationship("Organization", foreign_keys=[organization_id])
    responses: Mapped[list["WellbeingSurveyResponse"]] = relationship(
        "WellbeingSurveyResponse", back_populates="window", cascade="all, delete-orphan",
    )
    snapshots: Mapped[list["WellbeingPulseSnapshot"]] = relationship(
        "WellbeingPulseSnapshot", back_populates="window", cascade="all, delete-orphan",
    )
    __table_args__ = (
        UniqueConstraint("organization_id", "window_start", name="uq_wellbeing_survey_window_org_start"),
        CheckConstraint("window_end >= window_start", name="ck_wellbeing_survey_window_range"),
    )

    def __repr__(self) -> str:
        return f"<WellbeingSurveyWindow org={self.organization_id} {self.window_start}..{self.window_end}>"


class WellbeingSurveyResponse(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wellbeing_survey_responses"

    window_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wellbeing_survey_windows.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    question_key: Mapped[str] = mapped_column(String(50), nullable=False)
    response_value: Mapped[int] = mapped_column(Integer, nullable=False)

    window: Mapped["WellbeingSurveyWindow"] = relationship("WellbeingSurveyWindow", back_populates="responses")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    organization: Mapped["Organization"] = relationship("Organization", foreign_keys=[organization_id])

    __table_args__ = (
        UniqueConstraint("window_id", "user_id", "question_key", name="uq_wellbeing_survey_response_window_user_question"),
        CheckConstraint("response_value BETWEEN 1 AND 5", name="ck_wellbeing_survey_response_value_range"),
        CheckConstraint(
            "question_key IN ('stress_manageability', 'energy_recovery', 'connection_belonging', "
            "'workload_sustainability', 'workplace_comfort')",
            name="ck_wellbeing_survey_response_question_key",
        ),
    )

    def __repr__(self) -> str:
        return f"<WellbeingSurveyResponse window={self.window_id} user={self.user_id} q={self.question_key} v={self.response_value}>"

class WellbeingPulseSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One row per (window, branch_id) — branch_id NULL means the org-wide
    snapshot, branch_id set means that specific branch's snapshot. Both
    scopes share this table since it's the same shape of data, just a
    different population being aggregated — same reasoning as
    KPISnapshot's original branch/org split (before KPI itself was
    scrapped), applied here instead.
    """

    __tablename__ = "wellbeing_pulse_snapshots"

    window_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wellbeing_survey_windows.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # NULL = org-wide snapshot for this window. Set = branch-scoped.
    branch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("branches.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    stress_manageability_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    energy_recovery_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    connection_belonging_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    workload_sustainability_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    workplace_comfort_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    priority_support_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    needs_attention_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    doing_well_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    respondent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
    )

    #Realtionships
    
    # WellbeingPulseSnapshot
    window: Mapped["WellbeingSurveyWindow"] = relationship("WellbeingSurveyWindow", back_populates="snapshots")
    organization: Mapped["Organization"] = relationship("Organization", foreign_keys=[organization_id])

    __table_args__ = (
        UniqueConstraint("window_id", "branch_id", name="uq_wellbeing_pulse_snapshot_window_branch"),
    )

    def __repr__(self) -> str:
        scope = f"branch={self.branch_id}" if self.branch_id else "org-wide"
        return f"<WellbeingPulseSnapshot window={self.window_id} {scope} respondents={self.respondent_count}>"