"""
app/routers/posts.py

Post management routes.

Permission model:
  Post creation requires no permission — org-level posting is open to any
  org member; branch-scoped posting requires the poster to belong to that
  branch (or be super_admin). See check_post_creation_membership.

  post.update / post.delete — scope='both', checked against the post's
  branch_id (None branch_id => org-level scope check only, see
  check_post_scope).

  post.moderate — separate permission, used only when an admin acts on
  someone else's post/comment (delete-as-moderation). Authors deleting
  their own content don't need any permission grant — see
  _require_owner below.

  Likes / comments / shares / saves are self-service by default (a user
  manages their own reaction) — gated by get_current_user + org match
  only. Editing/deleting *your own* comment needs no permission; deleting
  *someone else's* comment requires post.moderate.

Clubs are group-chat only — posts have no club_id. See club/chat service
for club-scoped messaging.

Routes:
  POST   /organizations/{org_id}/posts                                    — create
  GET    /organizations/{org_id}/posts                                    — list (optional ?branch_id=)
  GET    /organizations/{org_id}/posts/saved                              — list my saved posts
  GET    /organizations/{org_id}/posts/users/{user_id}                    — list posts by user
  GET    /organizations/{org_id}/posts/{post_id}                          — get one
  PATCH  /organizations/{org_id}/posts/{post_id}                          — edit (author only)
  DELETE /organizations/{org_id}/posts/{post_id}                          — delete (author or moderator)

  POST   /organizations/{org_id}/posts/{post_id}/like                     — like
  DELETE /organizations/{org_id}/posts/{post_id}/like                     — unlike
  GET    /organizations/{org_id}/posts/{post_id}/likes                    — list likers

  GET    /organizations/{org_id}/posts/{post_id}/comments                 — list top-level comments
  POST   /organizations/{org_id}/posts/{post_id}/comments                 — add comment / reply
  PATCH  /organizations/{org_id}/posts/{post_id}/comments/{comment_id}    — edit own comment
  DELETE /organizations/{org_id}/posts/{post_id}/comments/{comment_id}    — delete (own, or moderator)

  POST   /organizations/{org_id}/posts/{post_id}/share                    — share

  POST   /organizations/{org_id}/posts/{post_id}/save                    — save
  DELETE /organizations/{org_id}/posts/{post_id}/save                    — unsave
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user, require_same_organization
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import PostComment
from app.schemas.post import (
    CommentCreateRequest,
    CommentListResponse,
    CommentResponse,
    CommentUpdateRequest,
    LikeActionResponse,
    PostCreateRequest,
    PostLikersListResponse,
    PostListResponse,
    PostResponse,
    PostUpdateRequest,
    SaveActionResponse,
    SavedPostsListResponse,
    ShareCreateRequest,
    ShareResponse,
)
from app.services.post import (
    add_comment,
    create_post,
    delete_comment,
    delete_post,
    edit_comment,
    edit_post,
    get_comments,
    get_post,
    get_post_likers,
    get_posts,
    get_posts_by_user,
    get_saved_posts,
    like_post,
    save_post,
    share_post,
    unlike_post,
    unsave_post,
)

from app.services.post._helpers import check_post_creation_membership, check_post_scope
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organizations/{org_id}/posts", tags=["posts"])


def _require_owner(resource_user_id: str, current_user: CurrentUser) -> bool:
    """Returns True if current_user owns the resource. Does not raise —
    caller decides whether to fall back to a moderator permission check."""
    return resource_user_id == current_user.user_id


# ── Create ───────────────────────────────────────────────────────────────────
@router.post(
    "",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a post",
    description=(
        "branch_id omitted/null = org-level post, open to any org member. "
        "branch_id set = must belong to that branch (or be super_admin). "
        "No permission grant required either way."
    ),
)
async def create_post_route(
    org_id: str,
    body: PostCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostResponse:
    require_same_organization(org_id, current_user)
    check_post_creation_membership(current_user=current_user, branch_id=body.branch_id)

    async with request_guard(db, "/organizations/{org_id}/posts"):
        post = await create_post(
            session=db,
            organization_id=org_id,
            user_id=current_user.user_id,
            branch_id=body.branch_id,
            content=body.content,
            media_url=body.media_url,
            media_type=body.media_type,
        )
        await db.commit()
        return PostResponse.model_validate(post)

# ── List ─────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=PostListResponse,
    status_code=status.HTTP_200_OK,
    summary="List posts in the organization",
    description="Optionally filter by ?branch_id=. Accessible to any authenticated org member.",
)
async def list_posts_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/posts"):
        posts, total = await get_posts(
            session=db, organization_id=org_id, branch_id=branch_id, offset=offset, limit=limit,
        )
        return PostListResponse(
            items=[PostResponse.model_validate(p) for p in posts], total=total, offset=offset, limit=limit,
        )


# ── Saved posts (must be registered before /{post_id} to avoid path clash) ──

@router.get(
    "/saved",
    response_model=SavedPostsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List my saved posts",
)
async def list_saved_posts_route(
    org_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SavedPostsListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/posts/saved"):
        posts, total = await get_saved_posts(
            session=db, organization_id=org_id, user_id=current_user.user_id, offset=offset, limit=limit,
        )
        return SavedPostsListResponse(
            items=[PostResponse.model_validate(p) for p in posts], total=total, offset=offset, limit=limit,
        )


# ── Get posts by user (must be registered before /{post_id} too) ────────────

@router.get(
    "/users/{user_id}",
    response_model=PostListResponse,
    status_code=status.HTTP_200_OK,
    summary="List posts by a specific user",
    description="All posts authored by the given user in this org, across branches.",
)
async def list_posts_by_user_route(
    org_id: str,
    user_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/posts/users/{user_id}"):
        posts, total = await get_posts_by_user(
            session=db, organization_id=org_id, user_id=user_id, offset=offset, limit=limit,
        )
        return PostListResponse(
            items=[PostResponse.model_validate(p) for p in posts], total=total, offset=offset, limit=limit,
        )


# ── Get one ──────────────────────────────────────────────────────────────────

@router.get("/{post_id}", response_model=PostResponse, status_code=status.HTTP_200_OK)
async def get_post_route(
    org_id: str,
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}"):
        post = await get_post(session=db, organization_id=org_id, post_id=post_id)
        return PostResponse.model_validate(post)


# ── Edit (author only — no moderator override; moderators use delete) ───────

@router.patch(
    "/{post_id}",
    response_model=PostResponse,
    status_code=status.HTTP_200_OK,
    summary="Edit a post",
    description="Author only. Editing someone else's post isn't a moderation action — deletion is.",
)
async def edit_post_route(
    org_id: str,
    post_id: str,
    body: PostUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}"):
        post = await get_post(session=db, organization_id=org_id, post_id=post_id)
        if not _require_owner(post.user_id, current_user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own posts.")

        updated = await edit_post(
            session=db, organization_id=org_id, post_id=post_id,
            content=body.content, media_url=body.media_url, media_type=body.media_type,
        )
        await db.commit()
        return PostResponse.model_validate(updated)


# ── Delete (author, or moderator with post.moderate) ─────────────────────────

@router.delete(
    "/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a post",
    description="Author can delete their own post freely. Deleting another user's post requires `post.moderate`.",
)
async def delete_post_route(
    org_id: str,
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}"):
        post = await get_post(session=db, organization_id=org_id, post_id=post_id)
        if not _require_owner(post.user_id, current_user):
            await check_post_scope(
                db=db, current_user=current_user, permission="post.moderate", branch_id=post.branch_id,
            )
        await delete_post(session=db, organization_id=org_id, post_id=post_id)
        await db.commit()


# ── Likes ────────────────────────────────────────────────────────────────────
@router.post("/{post_id}/like", response_model=LikeActionResponse, status_code=status.HTTP_200_OK)
async def like_post_route(
    org_id: str, post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LikeActionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/like"):
        liked, count = await like_post(session=db, organization_id=org_id, post_id=post_id, current_user=current_user)
        await db.commit()
        return LikeActionResponse(post_id=post_id, user_id=current_user.user_id, liked=liked, like_count=count, message="Liked.")


@router.delete("/{post_id}/like", response_model=LikeActionResponse, status_code=status.HTTP_200_OK)
async def unlike_post_route(
    org_id: str, post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> LikeActionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/like"):
        liked, count = await unlike_post(session=db, organization_id=org_id, post_id=post_id, current_user=current_user)
        await db.commit()
        return LikeActionResponse(post_id=post_id, user_id=current_user.user_id, liked=liked, like_count=count, message="Unliked.")


@router.get("/{post_id}/likes", response_model=PostLikersListResponse, status_code=status.HTTP_200_OK)
async def list_post_likers_route(
    org_id: str, post_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PostLikersListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/likes"):
        users, total = await get_post_likers(session=db, organization_id=org_id, post_id=post_id, offset=offset, limit=limit)
        return PostLikersListResponse(
            items=[{"user_id": u.id, "first_name": u.first_name, "last_name": u.last_name} for u in users],
            total=total, offset=offset, limit=limit,
        )


# ── Comments ─────────────────────────────────────────────────────────────────

@router.get("/{post_id}/comments", response_model=CommentListResponse, status_code=status.HTTP_200_OK)
async def list_comments_route(
    org_id: str, post_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CommentListResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/comments"):
        comments, total = await get_comments(session=db, organization_id=org_id, post_id=post_id, offset=offset, limit=limit)
        return CommentListResponse(
            items=[CommentResponse.model_validate(c) for c in comments], total=total, offset=offset, limit=limit,
        )

@router.post(
    "/{post_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED,
    summary="Add a comment, or reply to one",
)
async def add_comment_route(
    org_id: str, post_id: str, body: CommentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CommentResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/comments"):
        comment = await add_comment(
            session=db, organization_id=org_id, post_id=post_id, current_user=current_user,
            content=body.content, parent_comment_id=body.parent_comment_id,
        )
        await db.commit()
        return CommentResponse.model_validate(comment)

@router.patch(
    "/{post_id}/comments/{comment_id}", response_model=CommentResponse, status_code=status.HTTP_200_OK,
    summary="Edit your own comment",
)
async def edit_comment_route(
    org_id: str, post_id: str, comment_id: str, body: CommentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CommentResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/comments/{comment_id}"):
        # Ownership check happens here, not inside edit_comment — since
        # edit_comment doesn't filter by user_id, enforce it before calling.
        existing = (await db.execute(
            select(PostComment).where(PostComment.id == comment_id, PostComment.post_id == post_id)
        )).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found.")
        if not _require_owner(existing.user_id, current_user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own comments.")

        comment = await edit_comment(session=db, organization_id=org_id, post_id=post_id, comment_id=comment_id, content=body.content)
        await db.commit()
        return CommentResponse.model_validate(comment)


@router.delete(
    "/{post_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment",
    description="Own comment: always allowed. Someone else's comment: requires `post.moderate`.",
)
async def delete_comment_route(
    org_id: str, post_id: str, comment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/comments/{comment_id}"):
        existing = (await db.execute(
            select(PostComment).where(PostComment.id == comment_id, PostComment.post_id == post_id)
        )).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found.")

        if not _require_owner(existing.user_id, current_user):
            post = await get_post(session=db, organization_id=org_id, post_id=post_id)
            await check_post_scope(db=db, current_user=current_user, permission="post.moderate", branch_id=post.branch_id)

        await delete_comment(session=db, organization_id=org_id, post_id=post_id, comment_id=comment_id)
        await db.commit()


# ── Shares ───────────────────────────────────────────────────────────────────
@router.post("/{post_id}/share", response_model=ShareResponse, status_code=status.HTTP_201_CREATED)
async def share_post_route(
    org_id: str, post_id: str, body: ShareCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ShareResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/share"):
        await share_post(
            session=db, organization_id=org_id, post_id=post_id, current_user=current_user,
            destination_type=body.destination_type, destination_id=body.destination_id,
        )
        await db.commit()
        return ShareResponse(post_id=post_id, user_id=current_user.user_id, message="Shared.")
        
# ── Saves ────────────────────────────────────────────────────────────────────
@router.post("/{post_id}/save", response_model=SaveActionResponse, status_code=status.HTTP_200_OK)
async def save_post_route(
    org_id: str, post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SaveActionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/save"):
        saved = await save_post(session=db, organization_id=org_id, post_id=post_id, current_user=current_user)
        await db.commit()
        return SaveActionResponse(post_id=post_id, user_id=current_user.user_id, saved=saved, message="Saved.")


@router.delete("/{post_id}/save", response_model=SaveActionResponse, status_code=status.HTTP_200_OK)
async def unsave_post_route(
    org_id: str, post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SaveActionResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/posts/{post_id}/save"):
        saved = await unsave_post(session=db, organization_id=org_id, post_id=post_id, current_user=current_user)
        await db.commit()
        return SaveActionResponse(post_id=post_id, user_id=current_user.user_id, saved=saved, message="Unsaved.")