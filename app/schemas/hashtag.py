"""
app/schemas/hashtag.py
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.post import PostListResponse


class HashtagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tag_name: str
    total_post_count: int


class HashtagListResponse(BaseModel):
    items: list[HashtagResponse]
    total: int
    offset: int
    limit: int


class TrendingHashtagResponse(BaseModel):
    tag_name: str
    total_post_count: int
    trend_score: Decimal
    window_end: str


class TrendingHashtagsListResponse(BaseModel):
    items: list[TrendingHashtagResponse]