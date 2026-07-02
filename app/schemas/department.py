"""
app/schemas/department.py

Pydantic v2 schemas for department endpoints.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    avatar_initial: str | None = Field(default=None, max_length=10)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Department name must not be blank.")
        return v.strip()


class DepartmentUpdateRequest(BaseModel):
    """All fields optional — only provided fields are updated (PATCH semantics)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    avatar_initial: str | None = Field(default=None, max_length=10)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Department name must not be blank.")
        return v.strip() if v is not None else v

    @model_validator(mode="before")
    @classmethod
    def reject_readonly_fields(cls, values: dict) -> dict:
        readonly = {
            "id", "organization_id", "branch_id",
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
    def require_at_least_one_field(self) -> "DepartmentUpdateRequest":
        if self.name is None and self.description is None and self.avatar_initial is None:
            raise ValueError("Provide at least one field to update.")
        return self


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    avatar_initial: str | None
    organization_id: str
    branch_id: str | None
    member_count: int
    created_by: str | None


class DepartmentListResponse(BaseModel):
    """Paginated list of departments."""

    items: list[DepartmentResponse]
    total: int
    offset: int
    limit: int

class DepartmentMemberAssignResponse(BaseModel):
    """Returned after a successful department assignment."""
    user_id: str
    department_id: str
    organization_id: str
    message: str


class DepartmentMemberInfo(BaseModel):
    """Allow-list — deliberately excludes hashed_password and any other
    sensitive User fields. Add fields here only as needed by the client."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str
    last_name: str
    email: str
    avatar_url: str | None
    status: str


class DepartmentMembersListResponse(BaseModel):
    items: list[DepartmentMemberInfo]
    total: int
    offset: int
    limit: int
class DepartmentDeleteRequest(BaseModel):
    """
    transfer_to_department_id is required when the department still has
    members — they must be moved atomically before deletion so no user
    is ever left without a department.
    """
    transfer_to_department_id: str | None = Field(
        default=None,
        description=(
            "Required if the department has members. "
            "All members are moved here before deletion."
        ),
    )


class DepartmentDeleteResponse(BaseModel):
    department_id: str
    organization_id: str
    members_transferred: int
    message: str


class DepartmentHeadAssignResponse(BaseModel):
    department_id: str
    organization_id: str
    head_user_id: str
    message: str


class DepartmentHeadRemoveResponse(BaseModel):
    department_id: str
    organization_id: str
    message: str

