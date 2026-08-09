"""
app/routers/stories.py

Permission model:
  Creation — open self-service, same rule as posts: org-level story
  open to anyone, branch-level requires membership in that branch (or
  super_admin). No permission grant required either way.

  Visibility — org-level visible to everyone; branch-level visible to
  that branch's members, or story.view_all (scoped) / super_admin.

  Delete — own story always allowed; someone else's requires
  story.delete (mirrors post.moderate's pattern, using story.delete
  since that's what's in the seed — content category, scope='both').

Expiry: stories are excluded from all reads once expires_at has passed
(defensive filter), and physically removed by a purge job (see
app/jobs/media/story_purge.py) — not deleted synchronously on read.

Routes:
  POST   /organizations/{org_id}/stories                — create
  GET    /organizations/{org_id}/stories                 — list feed (optional ?branch_id=)
  GET    /organizations/{org_id}/stories/me               — my own stories
  GET    /organizations/{org_id}/stories/{story_id}       — get one
  DELETE /organizations/{org_id}/stories/{story_id}       — delete (own, or story.delete)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization, require_active_subscription
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.story import StoryCreateRequest, StoryListResponse, StoryResponse
from app.services.story import create_story, delete_story, get_my_stories, get_stories, get_story
from app.services.story._helpers import check_story_branch_access, check_story_creation_membership

router = APIRouter(prefix="/organizations/{org_id}/stories", tags=["stories"])


@router.post("", response_model=StoryResponse, status_code=status.HTTP_201_CREATED)
async def create_story_route(
    org_id: str, body: StoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
) -> StoryResponse:
    require_same_organization(org_id, current_user)
    check_story_creation_membership(current_user=current_user, branch_id=body.branch_id)

    async with request_guard(db, "/organizations/{org_id}/stories"):
        story = await create_story(
            session=db, organization_id=org_id, user_id=current_user.user_id, branch_id=body.branch_id,
            media_url=body.media_url, media_type=body.media_type,
        )
        await db.commit()
        return StoryResponse.model_validate(story)

@router.get("", response_model=StoryListResponse, status_code=status.HTTP_200_OK)
async def list_stories_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    scope: str = Query(default="branch_and_org", pattern="^(org_only|branch_and_org)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StoryListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/stories"):
        org_only = scope == "org_only"
        stories, total = await get_stories(
            session=db, organization_id=org_id,
            branch_id=None if org_only else branch_id,
            org_only=org_only,
            viewer_branch_id=current_user.branch_id, is_super_admin=(current_user.role == "super_admin"),
            offset=offset, limit=limit,
        )
        return StoryListResponse(items=[StoryResponse.model_validate(s) for s in stories], total=total, offset=offset, limit=limit)

@router.get("/me", response_model=StoryListResponse, status_code=status.HTTP_200_OK)
async def my_stories_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StoryListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/stories/me"):
        stories, total = await get_my_stories(session=db, user_id=current_user.user_id, offset=offset, limit=limit)
        return StoryListResponse(items=[StoryResponse.model_validate(s) for s in stories], total=total, offset=offset, limit=limit)


@router.get("/{story_id}", response_model=StoryResponse, status_code=status.HTTP_200_OK)
async def get_story_route(
    org_id: str, story_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StoryResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/stories/{story_id}"):
        story = await get_story(session=db, organization_id=org_id, story_id=story_id)
        await check_story_branch_access(db=db, current_user=current_user, story=story)
        return StoryResponse.model_validate(story)


@router.delete("/{story_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_story_route(
    org_id: str, story_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/stories/{story_id}"):
        story = await get_story(session=db, organization_id=org_id, story_id=story_id)

        is_moderator = False
        if story.user_id != current_user.user_id:
            from app.auth.dependencies import require_scoped_permission
            try:
                await require_scoped_permission(
                    db=db, current_user=current_user, permission="story.delete", target_branch_id=story.branch_id,
                )
                is_moderator = True
            except HTTPException:
                pass  # fall through — delete_story raises the real 403

        await delete_story(session=db, organization_id=org_id, story_id=story_id, requesting_user_id=current_user.user_id, is_moderator=is_moderator)
        await db.commit()