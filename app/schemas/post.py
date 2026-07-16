"""
app/schemas/post.py
"""

from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime




# ── Post ─────────────────────────────────────────────────────────────────────

class PostCreateRequest(BaseModel):
    # None = org-level feed post. Set = branch-scoped post.
    branch_id: str | None = Field(default=None)
   
    content: str | None = Field(default=None, max_length=10_000)
    # TODO(s3): once S3 is wired up, this becomes a presigned-upload flow —
    # client uploads directly to a presigned URL, then sends the resulting
    # object URL here. For now this just accepts a URL as-is.
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = Field(default=None, max_length=50)  # image | video | none


class PostUpdateRequest(BaseModel):
    content: str | None = Field(default=None, max_length=10_000)
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = Field(default=None, max_length=50)



class PostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    organization_id: str
    branch_id: str | None
    content: str | None
    media_url: str | None
    media_type: str | None
    like_count: int
    comment_count: int
    share_count: int  # new
    created_at: datetime
    updated_at: datetime

class PostListResponse(BaseModel):
    items: list[PostResponse]
    total: int
    offset: int
    limit: int


# ── Likes ────────────────────────────────────────────────────────────────────

class LikeActionResponse(BaseModel):
    post_id: str
    user_id: str
    liked: bool
    like_count: int
    message: str


class PostLikerInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    first_name: str
    last_name: str


class PostLikersListResponse(BaseModel):
    items: list[PostLikerInfo]
    total: int
    offset: int
    limit: int


# ── Comments ─────────────────────────────────────────────────────────────────

class CommentCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    parent_comment_id: str | None = Field(default=None, description="Set to reply to another comment.")


class CommentUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class CommentReplyResponse(BaseModel):
    """A reply — one level deep, no further nested replies (by design,
    see comment_service.get_comments)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    post_id: str
    user_id: str
    content: str | None
    parent_comment_id: str | None
    created_at: datetime
    updated_at: datetime


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    post_id: str
    user_id: str
    content: str | None
    parent_comment_id: str | None
    created_at: datetime
    updated_at: datetime
    replies: list[CommentReplyResponse] = []


class CommentListResponse(BaseModel):
    items: list[CommentResponse]
    total: int
    offset: int
    limit: int


# ── Shares ───────────────────────────────────────────────────────────────────
class ShareCreateRequest(BaseModel):
    # Both omitted = signal-only share (counted, nothing else happens).
    # Both set = drops a message into that club/event's group chat
    # referencing the post.
    destination_type: str | None = Field(default=None, description="'club' or 'event'")
    destination_id: str | None = Field(default=None)


class ShareResponse(BaseModel):
    post_id: str
    user_id: str
    message: str


# ── Saves ────────────────────────────────────────────────────────────────────

class SaveActionResponse(BaseModel):
    post_id: str
    user_id: str
    saved: bool
    message: str


class SavedPostsListResponse(BaseModel):
    items: list[PostResponse]
    total: int
    offset: int
    limit: int