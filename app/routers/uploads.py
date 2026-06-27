"""
Upload routes — issue presigned URLs so clients upload media directly
to S3/R2, never through this server.

This route is protected: only authenticated users can request an
upload URL, since object_key is namespaced by user/org to prevent
one user from overwriting another's files.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import CurrentUser, get_current_user
from app.core.storage import generate_presigned_upload_url

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4"}


class PresignRequest(BaseModel):
    content_type: str
    purpose: str = "post_media"  # post_media | avatar | event_image | story


class PresignResponse(BaseModel):
    upload_url: str
    public_url: str
    object_key: str
    expires_in: int


@router.post("/presign", response_model=PresignResponse)
async def presign_upload(
    payload: PresignRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Request a short-lived presigned URL to upload a file directly to
    object storage. The client PUTs the raw file bytes to `upload_url`,
    then stores `public_url` as the media reference (e.g. on a post).
    """
    if payload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content_type. Allowed: {sorted(ALLOWED_CONTENT_TYPES)}",
        )

    object_key = (
        f"{current_user.organization_id}/{payload.purpose}/"
        f"{current_user.user_id}/{uuid.uuid4()}"
    )

    result = await generate_presigned_upload_url(
        object_key=object_key, content_type=payload.content_type
    )
    return PresignResponse(**result)
