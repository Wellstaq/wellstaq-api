"""
app/schemas/department.py

Pydantic v2 schemas for department endpoints.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


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