"""
app/schemas/category.py
"""

from pydantic import BaseModel, ConfigDict


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    badge_color: str | None


class CategoryListResponse(BaseModel):
    items: list[CategoryResponse]


class HealthCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class HealthCategoryListResponse(BaseModel):
    items: list[HealthCategoryResponse]