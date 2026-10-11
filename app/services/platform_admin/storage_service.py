"""
app/services/platform_admin/storage_service.py

Presigned uploads for admin-owned media — admin profile avatars and
platform event cover images. Deliberately NOT routed through
app/services/storage/upload_service.py: that path tracks every upload
as a MediaAsset row for the pending/orphan-sweep system, but
MediaAsset.organization_id is required (NOT NULL) — and admin-owned
content has no organization to attribute it to. Admin-side upload
volume is small and admin-trusted, so skipping that tracking (no
pending-sweep, no orphan cleanup) is an acceptable trade-off rather
than loosening a NOT NULL constraint that exists for real multi-tenant
safety elsewhere.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status

from app.core.storage import generate_presigned_upload_url, get_object_url
from app.services.storage._helpers import ALLOWED_IMAGE_TYPES, content_type_to_extension

# Images only — avatars and event covers, never video.
ADMIN_UPLOAD_DOMAINS = ("admin-avatars", "platform-events")


def request_admin_upload(*, domain: str, content_type: str) -> dict:
    if domain not in ADMIN_UPLOAD_DOMAINS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"domain must be one of {ADMIN_UPLOAD_DOMAINS}.")
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported content_type '{content_type}'. Allowed: {ALLOWED_IMAGE_TYPES}.")

    extension = content_type_to_extension(content_type)
    object_key = f"{domain}/{uuid.uuid4()}.{extension}"
    upload_url = generate_presigned_upload_url(object_key=object_key, content_type=content_type)
    media_url = get_object_url(object_key=object_key)
    return {"upload_url": upload_url, "object_key": object_key, "media_url": media_url, "content_type": content_type}
