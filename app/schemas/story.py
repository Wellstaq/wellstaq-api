"""
app/schemas/story.py
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StoryCreateRequest(BaseModel):
    branch_id: str | None = Field(default=None, description="Null = org-level story.")
    media_url: str = Field(..., max_length=2048)
    media_type: str = Field(default="image", description="'image' or 'video'")


class StoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    organization_id: str
    branch_id: str | None
    media_url: str
    media_type: str
    like_count: int
    expires_at: datetime
    created_at: datetime


class StoryLikeActionResponse(BaseModel):
    story_id: str
    user_id: str
    liked: bool
    like_count: int


class StoryListResponse(BaseModel):
    items: list[StoryResponse]
    total: int
    offset: int
    limit: int