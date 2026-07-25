"""
app/services/storage/_helpers.py
"""

from __future__ import annotations

import uuid

ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
ALLOWED_VIDEO_TYPES = ("video/mp4", "video/quicktime", "video/webm")
ALLOWED_MEDIA_TYPES = ALLOWED_IMAGE_TYPES + ALLOWED_VIDEO_TYPES

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_VIDEO_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

# Size limits aren't enforceable at presign time — S3 doesn't check file
# size until the actual PUT happens. Enforce these via a bucket policy /
# CORS rule with a Content-Length-Range condition (presigned POST, not
# PUT, supports this natively) if hard limits matter — see note in
# generate_upload_url below. For now these constants exist as documented
# intent even though this PUT-based flow can't enforce them server-side.


def build_object_key(*, domain: str, organization_id: str, extension: str) -> str:
    """
    domain: 'posts' | 'stories' | 'chat' | 'avatars' — keeps S3 objects
    organized by feature, mirrors how  DB tables are separated.
    organization_id: included so objects are trivially attributable/
    auditable per-tenant even though S3 has no tenant concept itself.
    A random UUID, not the DB row's own id, is used as the filename —
    the DB row doesn't exist yet at upload time (upload happens BEFORE
    the Post/Story/Message row is created), so there's no id to key off.
    """
    return f"{domain}/{organization_id}/{uuid.uuid4()}.{extension}"


def content_type_to_extension(content_type: str) -> str:
    mapping = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
        "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
    }
    return mapping.get(content_type, "bin")