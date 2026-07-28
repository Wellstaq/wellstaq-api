"""
app/routers/hashtags.py

Hashtag discovery routes. No mutation endpoints — hashtags are created
implicitly via post content (see app/services/hashtag/hashtag_service.py
sync_post_hashtags), never directly by a user action.

Routes:
  GET /organizations/{org_id}/hashtags/search?q=            — prefix search
  GET /organizations/{org_id}/hashtags/trending              — trending list
  GET /organizations/{org_id}/hashtags/{tag_name}/posts      — posts using a tag
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization, require_active_subscription
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.hashtag import (
    HashtagListResponse,
    HashtagResponse,
    TrendingHashtagResponse,
    TrendingHashtagsListResponse,
)
from app.schemas.post import PostListResponse, PostResponse
from app.services.hashtag import get_posts_by_hashtag, get_trending_hashtags, search_hashtags

router = APIRouter(prefix="/organizations/{org_id}/hashtags", tags=["hashtags"])


@router.get("/search", response_model=HashtagListResponse, status_code=status.HTTP_200_OK)
async def search_hashtags_route(
    org_id: str,
    q: str = Query(..., min_length=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> HashtagListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/hashtags/search"):
        hashtags, total = await search_hashtags(session=db, organization_id=org_id, query=q, offset=offset, limit=limit)
        return HashtagListResponse(
            items=[HashtagResponse.model_validate(h) for h in hashtags], total=total, offset=offset, limit=limit,
        )


@router.get("/trending", response_model=TrendingHashtagsListResponse, status_code=status.HTTP_200_OK)
async def trending_hashtags_route(
    org_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> TrendingHashtagsListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/hashtags/trending"):
        rows = await get_trending_hashtags(session=db, organization_id=org_id, limit=limit)
        return TrendingHashtagsListResponse(
            items=[
                TrendingHashtagResponse(
                    tag_name=hashtag.tag_name,
                    total_post_count=hashtag.total_post_count,
                    trend_score=snapshot.trend_score,
                    window_end=snapshot.window_end.isoformat(),
                )
                for hashtag, snapshot in rows
            ]
        )


@router.get("/{tag_name}/posts", response_model=PostListResponse, status_code=status.HTTP_200_OK)
async def posts_by_hashtag_route(
    org_id: str,
    tag_name: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/hashtags/{tag_name}/posts"):
        posts, total = await get_posts_by_hashtag(session=db, organization_id=org_id, tag_name=tag_name, offset=offset, limit=limit)
        return PostListResponse(
            items=[PostResponse.model_validate(p) for p in posts], total=total, offset=offset, limit=limit,
        )