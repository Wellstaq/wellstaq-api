"""
app/schemas/challenge.py
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChallengeCreateRequest(BaseModel):
    branch_id: str | None = Field(default=None, description="Null = org-level challenge.")
    wellbeing_challenge_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    metric_type: str | None = None
    target_type: str = Field(default="cumulative", description="'cumulative' or 'daily_minimum'")
    target_value: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_target_type(self):
        if self.target_type not in ("cumulative", "daily_minimum"):
            raise ValueError("target_type must be 'cumulative' or 'daily_minimum'.")
        if self.target_type == "daily_minimum" and self.end_date is None:
            raise ValueError("daily_minimum challenges require an end_date to know the evaluation window.")
        return self


class ChallengeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    metric_type: str | None = None
    target_type: str | None = None
    target_value: Decimal | None = Field(default=None, gt=0)
    status: str | None = None  # draft | active | completed | cancelled | archived

    @model_validator(mode="after")
    def validate_target_type(self):
        if self.target_type is not None and self.target_type not in ("cumulative", "daily_minimum"):
            raise ValueError("target_type must be 'cumulative' or 'daily_minimum'.")
        return self


class ChallengeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    branch_id: str | None
    wellbeing_challenge_id: str | None
    name: str
    description: str | None
    start_date: date | None
    end_date: date | None
    status: str
    metric_type: str | None
    target_type: str
    target_value: Decimal | None
    created_by: str | None
    created_at: datetime


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
    completed_at: datetime | None
    joined_at: datetime


class ParticipantListResponse(BaseModel):
    items: list[ParticipantInfo]
    total: int
    offset: int
    limit: int


class ParticipantCompleteRequest(BaseModel):
    """Manual admin override — see TODO(background-job) in
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