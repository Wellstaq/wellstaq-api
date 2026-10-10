"""
app/schemas/challenge.py
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.constants import SUBMISSION_REVIEW_STATUSES, TRACKING_METHODS

class ChallengeCreateRequest(BaseModel):
    branch_id: str | None = Field(default=None, description="Null = org-level challenge.")
    wellbeing_challenge_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)  # new
    start_date: date
    end_date: date
    metric_type: str | None = None
    target_type: str = Field(default="cumulative", description="'cumulative' or 'daily_minimum'")
    target_value: Decimal | None = Field(default=None, gt=0)
    tracking_method: str = Field(
        default="self_reported",
        description="'automatic', 'self_reported', 'evidence_based', or 'platform_recorded'.",
    )
    activity_label: str | None = Field(
        default=None, description="The specific defined activity, e.g. 'Prepare a healthy meal containing vegetables.'",
    )
    evidence_required: bool = Field(default=False, description="Only meaningful when tracking_method='evidence_based'.")
    requires_approval: bool = Field(
        default=False,
        description="Evidence-based submissions need admin approval before counting toward progress.",
    )

    @model_validator(mode="after")
    def validate_target_type(self):
        if self.target_type not in ("cumulative", "daily_minimum"):
            raise ValueError("target_type must be 'cumulative' or 'daily_minimum'.")
        if self.target_type == "daily_minimum" and self.end_date is None:
            raise ValueError("daily_minimum challenges require an end_date to know the evaluation window.")
        return self

    @model_validator(mode="after")
    def validate_tracking_method(self):
        if self.tracking_method not in TRACKING_METHODS:
            raise ValueError(f"tracking_method must be one of {TRACKING_METHODS}.")
        if self.tracking_method != "evidence_based" and (self.evidence_required or self.requires_approval):
            raise ValueError("evidence_required/requires_approval only apply to tracking_method='evidence_based'.")
        return self


class ChallengeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)  # new
    start_date: date | None = None
    end_date: date | None = None
    metric_type: str | None = None
    target_type: str | None = None
    target_value: Decimal | None = Field(default=None, gt=0)
    status: str | None = None
    tracking_method: str | None = None
    activity_label: str | None = None
    evidence_required: bool | None = None
    requires_approval: bool | None = None

    @model_validator(mode="after")
    def validate_target_type(self):
        if self.target_type is not None and self.target_type not in ("cumulative", "daily_minimum"):
            raise ValueError("target_type must be 'cumulative' or 'daily_minimum'.")
        return self

    @model_validator(mode="after")
    def validate_tracking_method(self):
        if self.tracking_method is not None and self.tracking_method not in TRACKING_METHODS:
            raise ValueError(f"tracking_method must be one of {TRACKING_METHODS}.")
        return self


class ChallengeResponse(BaseModel):
    id: str
    organization_id: str
    branch_id: str | None
    wellbeing_challenge_id: str | None
    name: str
    description: str | None
    image_url: str | None
    start_date: date | None
    end_date: date | None
    status: str
    metric_type: str | None
    target_type: str
    target_value: float | None
    tracking_method: str
    activity_label: str | None
    evidence_required: bool
    requires_approval: bool
    created_by: str | None
    created_at: datetime

    # Computed — set post-query in get_challenges, not persisted columns.
    # completion_rate is None when participant_count is 0 (no divide-by-zero,
    # and lets the frontend distinguish "0% completed" from "nobody joined yet").
    participant_count: int = 0
    completion_rate: float | None = None

    model_config = ConfigDict(from_attributes=True)


class ChallengeStatsResponse(BaseModel):
    active_challenges: int
    total_participants: int
    completion_rate: float

class ChallengeListResponse(BaseModel):
    items: list[ChallengeResponse]
    total: int
    offset: int
    limit: int


class ParticipantJoinResponse(BaseModel):
    challenge_id: str
    user_id: str
    message: str


class ParticipantInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    first_name: str
    last_name: str
    rank: int | None
    score: int | None
    completed_at: datetime | None
    joined_at: datetime


class ParticipantScoreRequest(BaseModel):
    """Admin-assigned quality score for competition-style challenges
    (e.g. a cooking challenge judged on submission quality, not just
    completion count)."""
    score: int = Field(..., ge=0, le=100)


class ParticipantListResponse(BaseModel):
    items: list[ParticipantInfo]
    total: int
    offset: int
    limit: int


class ParticipantCompleteRequest(BaseModel):
    """Manual admin override — see (background-job) in
    participant_service.py for the real computation path."""
    rank: int | None = Field(default=None, gt=0)


class ProgressSubmitRequest(BaseModel):
    value: Decimal = Field(..., ge=0)
    source: str = Field(default="mobile", max_length=30)

class ProgressEntryResponse(BaseModel):
    entry_date: date | None = None
    value: Decimal
    source: str
    already_completed: bool = False
    message: str | None = None


class SubmissionCreateRequest(BaseModel):
    """Evidence-based progress submission — tracking_method='evidence_based'
    only. Counts toward progress immediately (review_status='approved') if
    the challenge's requires_approval is False; otherwise sits 'pending'
    until an admin reviews it."""
    photo_url: str | None = Field(default=None, max_length=2048)
    note: str | None = Field(default=None, max_length=2000)


class SubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    challenge_id: str
    user_id: str
    entry_date: date
    photo_url: str | None
    note: str | None
    review_status: str
    rejection_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class SubmissionReviewerInfo(BaseModel):
    """Submission joined with the submitting participant's name, for the
    admin review queue — avoids a separate lookup per row."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    first_name: str
    last_name: str
    entry_date: date
    photo_url: str | None
    note: str | None
    review_status: str
    rejection_reason: str | None


class SubmissionListResponse(BaseModel):
    items: list[SubmissionReviewerInfo]
    total: int
    offset: int
    limit: int


class SubmissionReviewRequest(BaseModel):
    action: str = Field(..., description="'approve' or 'reject'.")
    reason: str | None = Field(default=None, max_length=2000, description="Required when action='reject'.")

    @model_validator(mode="after")
    def validate_action(self):
        if self.action not in ("approve", "reject"):
            raise ValueError("action must be 'approve' or 'reject'.")
        if self.action == "reject" and not self.reason:
            raise ValueError("reason is required when rejecting a submission.")
        return self


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: str
    first_name: str
    last_name: str
    score: int | None
    completed_at: datetime | None
    current_value: Decimal
    percent_complete: Decimal | None


class LeaderboardResponse(BaseModel):
    items: list[LeaderboardEntry]
    total: int
    offset: int
    limit: int


class DailyProgressEntry(BaseModel):
    entry_date: date
    value: Decimal
    met_target: bool | None  # None if challenge has no target_value to compare against
    review_status: str  # 'pending'/'approved'/'rejected' — only 'approved' counts toward current_value/percent_complete


class ParticipantProgressResponse(BaseModel):
    challenge_id: str
    user_id: str
    target_type: str
    target_value: Decimal | None
    current_value: Decimal
    percent_complete: Decimal | None
    completed_at: datetime | None
    days_remaining: int | None
    daily_entries: list[DailyProgressEntry]  # new
    days_met_target: int | None  # new — daily_minimum's real "progress" number
    days_logged: int  # new — how many days have any entry at all

class TrendData(BaseModel):
    current: int
    previous: int
    change_pct: float

class ChallengeStatsResponse(BaseModel):
    active_challenges: int
    active_challenges_trend: TrendData | None
    total_participants: int
    total_participants_trend: TrendData | None
    completion_rate: float
    completion_rate_trend: TrendData | None
    # Live counts, no trend — the admin dashboard's "Completed"/"Upcoming"
    # summary cards.
    completed_challenges: int
    upcoming_challenges: int