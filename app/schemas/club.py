"""
app/schemas/club.py

Pydantic v2 schemas for club endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VALID_PRIVACY = {"public", "private"}


class ClubCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    privacy: str = Field(default="public")

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Club name must not be blank.")
        return v.strip()

    @field_validator("privacy")
    @classmethod
    def privacy_must_be_valid(cls, v: str) -> str:
        if v not in VALID_PRIVACY:
            raise ValueError(f"privacy must be one of: {', '.join(sorted(VALID_PRIVACY))}.")
        return v


class ClubUpdateRequest(BaseModel):
    """All fields optional — PATCH semantics."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    privacy: str | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Club name must not be blank.")
        return v.strip() if v is not None else v

    @field_validator("privacy")
    @classmethod
    def privacy_must_be_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_PRIVACY:
            raise ValueError(f"privacy must be one of: {', '.join(sorted(VALID_PRIVACY))}.")
        return v

    @model_validator(mode="before")
    @classmethod
    def reject_readonly_fields(cls, values: dict) -> dict:
        readonly = {
            "id", "organization_id", "branch_id", "leader_id",
            "created_by", "member_count", "created_at", "updated_at",
        }
        sent = readonly & set(values.keys())
        if sent:
            raise ValueError(
                f"The following fields are read-only and cannot be updated: "
                f"{', '.join(sorted(sent))}."
            )
        return values

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "ClubUpdateRequest":
        if self.name is None and self.description is None and self.privacy is None:
            raise ValueError("Provide at least one field to update.")
        return self


class ClubResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    privacy: str
    organization_id: str
    branch_id: str
    member_count: int
    created_by: str | None
    leader_id: str | None  # user who created and owns the club


class ClubListResponse(BaseModel):
    items: list[ClubResponse]
    total: int
    offset: int
    limit: int


class ClubDeleteResponse(BaseModel):
    club_id: str
    organization_id: str
    message: str


class ClubMemberInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str
    last_name: str
    email: str
    avatar_url: str | None
    status: str


class ClubMembersListResponse(BaseModel):
    items: list[ClubMemberInfo]
    total: int
    offset: int
    limit: int


class ClubJoinResponse(BaseModel):
    user_id: str
    club_id: str
    organization_id: str
    message: str


class ClubLeaveResponse(BaseModel):
    user_id: str
    club_id: str
    organization_id: str
    message: str


class ClubRemoveMemberResponse(BaseModel):
    """Returned when the club leader removes a member."""
    user_id: str
    club_id: str
    organization_id: str
    message: str