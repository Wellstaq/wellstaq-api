"""
app/services/storage/upload_service.py

Shared presigned-upload flow, reused by posts, stories, chat, and
avatars. Each domain calls request_upload() to get a presigned URL
before the client uploads, then confirm_upload() to verify the object
actually landed in S3 before trusting the client's "done" signal and
using the URL in a DB row.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.core.storage import generate_presigned_upload_url, get_object_url, object_exists
from app.services.storage._helpers import ALLOWED_MEDIA_TYPES, build_object_key, content_type_to_extension


def request_upload(*, domain: str, organization_id: str, content_type: str) -> dict:
    if content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported content_type '{content_type}'. Allowed: {ALLOWED_MEDIA_TYPES}.",
        )

    extension = content_type_to_extension(content_type)
    object_key = build_object_key(domain=domain, organization_id=organization_id, extension=extension)
    upload_url = generate_presigned_upload_url(object_key=object_key, content_type=content_type)
    final_url = get_object_url(object_key=object_key)

    return {"upload_url": upload_url, "object_key": object_key, "media_url": final_url, "content_type": content_type}


def confirm_upload(*, object_key: str) -> bool:
    """Returns True if the object actually exists in S3 — callers
    (create_post, create_story, etc.) should reject the request if this
    is False rather than trust a client claiming 'I uploaded it.'"""
    return object_exists(object_key=object_key)