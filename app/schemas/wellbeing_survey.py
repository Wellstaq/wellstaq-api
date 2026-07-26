"""
app/schemas/wellbeing_survey.py
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ── Admin config ─────────────────────────────────────────────────────────────

class SurveyCadenceRequest(BaseModel):
    cadence: str = Field(..., description="'weekly' | 'biweekly' | 'monthly'")


class SurveyConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    organization_id: str
    cadence: str
    is_active: bool


# ── Member submission ────────────────────────────────────────────────────────

class SurveySubmitRequest(BaseModel):
    answers: dict[str, int] = Field(
        ..., description="Map of question_key -> 1-5 response. All 5 fixed questions required."
    )


class SurveyQuestionInfo(BaseModel):
    question_key: str
    question_text: str


class OpenWindowResponse(BaseModel):
    """What a member sees when checking if there's a survey to answer."""
    window_id: str
    window_start: date
    window_end: date
    already_responded: bool
    questions: list[SurveyQuestionInfo]


# ── Live pulse (currently open window) ──────────────────────────────────────

class PulseQuestionResult(BaseModel):
    percent: Decimal | None
    status: str | None


class LivePulseResponse(BaseModel):
    window_id: str
    respondent_count: int
    stress_manageability: PulseQuestionResult
    energy_recovery: PulseQuestionResult
    connection_belonging: PulseQuestionResult
    workload_sustainability: PulseQuestionResult
    workplace_comfort: PulseQuestionResult
    priority_support_pct: Decimal | None
    needs_attention_pct: Decimal | None
    doing_well_pct: Decimal | None


# ── Snapshot (closed windows) ────────────────────────────────────────────────

class PulseSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    window_id: str
    stress_manageability_pct: Decimal | None
    energy_recovery_pct: Decimal | None
    connection_belonging_pct: Decimal | None
    workload_sustainability_pct: Decimal | None
    workplace_comfort_pct: Decimal | None
    priority_support_pct: Decimal | None
    needs_attention_pct: Decimal | None
    doing_well_pct: Decimal | None
    respondent_count: int
    computed_at: datetime


class PulseSnapshotListResponse(BaseModel):
    items: list[PulseSnapshotResponse]


# ── Personal history (self-only) ─────────────────────────────────────────────

class MyResponseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    window_id: str
    question_key: str
    response_value: int
    created_at: datetime


class MyResponseListResponse(BaseModel):
    items: list[MyResponseItem]