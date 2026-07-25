"""
app/routers/storages.py

Generic presigned-upload endpoint, shared across every media-accepting
feature (posts, stories, chat, avatars). The client calls this FIRST,
uploads directly to S3 using the returned upload_url, THEN calls the
actual create_post/create_story/etc. endpoint with media_url — those
endpoints are responsible for calling confirm_upload() before trusting
the URL (see create_post.py update below).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.auth.dependencies import CurrentUser, get_current_user
from app.schemas.storage import UploadRequestSchema, UploadResponse
from app.services.storage import request_upload

router = APIRouter(prefix="/storage", tags=["storage"])

VALID_DOMAINS = ("posts", "stories", "chat", "avatars")


@router.post("/upload-url", response_model=UploadResponse, status_code=status.HTTP_200_OK)
async def request_upload_url_route(
    body: UploadRequestSchema,
    current_user: CurrentUser = Depends(get_current_user),
) -> UploadResponse:
    from fastapi import HTTPException
    if body.domain not in VALID_DOMAINS:
        raise HTTPException(400, f"domain must be one of {VALID_DOMAINS}.")

    result = request_upload(domain=body.domain, organization_id=current_user.organization_id, content_type=body.content_type)
    return UploadResponse(**result)