"""
app/schemas/wellbeing.py
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ── Lookups (read-only) ──────────────────────────────────────────────────────

class WellbeingDimensionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class WellbeingChallengeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class WellbeingDimensionListResponse(BaseModel):
    items: list[WellbeingDimensionResponse]


class WellbeingChallengeListResponse(BaseModel):
    items: list[WellbeingChallengeResponse]


# ── Organization opt-in ──────────────────────────────────────────────────────

class OrgChallengeOptInRequest(BaseModel):
    wellbeing_challenge_id: str


class OrgChallengeOptInResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    wellbeing_challenge_id: str


class OrgChallengeOptInListResponse(BaseModel):
    items: list[OrgChallengeOptInResponse]


# ── Personal baseline (self-only) ────────────────────────────────────────────

class BaselineSubmitRequest(BaseModel):
    dimension: str = Field(..., description="mood | stress | energy | work_life_balance")
    level: str = Field(..., description="good | bad | stressed | tired")
    reason: str | None = Field(default=None, max_length=100)


class BaselineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dimension: str
    level: str
    reason: str | None
    recorded_at: datetime


class BaselineListResponse(BaseModel):
    items: list[BaselineResponse]


# ── Personal health assessment (self-only) ───────────────────────────────────

class AssessmentSubmitRequest(BaseModel):
    category_id: str | None = None
    risk_score: Decimal | None = Field(default=None, ge=0, le=100)
    status: str = Field(default="pending", description="pending | completed | reviewed")


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    category_id: str | None
    risk_score: Decimal | None
    status: str
    assessed_at: datetime | None


class AssessmentListResponse(BaseModel):
    items: list[AssessmentResponse]


# ── Wellbeing score — personal + aggregate ───────────────────────────────────

class WellbeingScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    dimension_id: str
    score: Decimal
    period: str


class WellbeingScoreListResponse(BaseModel):
    items: list[WellbeingScoreResponse]


class WellbeingAggregateResponse(BaseModel):
    """Aggregate-only — never breaks down to individual scores. See
    ANONYMITY_THRESHOLD in the service layer."""
    dimension_id: str
    period: str
    average_score: Decimal | None
    participant_count: int
    suppressed: bool = Field(description="True if participant_count was below the anonymity threshold — average_score will be null.")