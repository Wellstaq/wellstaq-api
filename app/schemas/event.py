"""
app/schemas/event.py
"""



from datetime import date, time as time_type

from pydantic import BaseModel, ConfigDict, Field


class EventCreateRequest(BaseModel):
    branch_id: str = Field(..., min_length=1, description="Required — every event must belong to a branch.")
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    start_date: date
    end_date: date | None = None
    recurrence_rule: str | None = None
    time: time_type


class EventUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    start_date: date | None = None
    end_date: date | None = None
    recurrence_rule: str | None = None
    time: time_type | None = None
    status: str | None = None  # scheduled | ongoing | completed | cancelled

class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str | None
    image_url: str | None
    start_date: date
    end_date: date | None
    recurrence_rule: str | None
    time: time_type
    status: str
    organization_id: str
    branch_id: str
    created_by: str | None
    participant_count: int = 0

class EventListResponse(BaseModel):
    items: list[EventResponse]
    total: int
    offset: int
    limit: int


class EventParticipantInfo(BaseModel):
    """Allow-list of participant + user fields — no sensitive User columns."""
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    first_name: str
    last_name: str
    email: str
    status: str | None
    attended: bool
    invited_at: str | None


class EventParticipantsListResponse(BaseModel):
    items: list[EventParticipantInfo]
    total: int
    offset: int
    limit: int


class ParticipantJoinRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    is_invite: bool = Field(default=False, description="True if an admin is inviting this user; sets invited_at.")


class ParticipantStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="invited | accepted | declined | pending")


class ParticipantAttendanceUpdateRequest(BaseModel):
    attended: bool


class ParticipantActionResponse(BaseModel):
    event_id: str
    user_id: str
    message: str