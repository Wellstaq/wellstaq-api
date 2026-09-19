"""Validate post image galleries and single videos."""
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from app.services.storage._helpers import ALLOWED_IMAGE_TYPES, ALLOWED_VIDEO_TYPES, content_type_to_extension

IMAGE_EXTENSIONS = {content_type_to_extension(t) for t in ALLOWED_IMAGE_TYPES}
VIDEO_EXTENSIONS = {content_type_to_extension(t) for t in ALLOWED_VIDEO_TYPES}


def validate_post_media(urls: list[str], media_type: str | None) -> None:
    if len(urls) > 15 or len(set(urls)) != len(urls):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A post can contain at most 15 distinct images.")
    if media_type not in (None, "image", "video"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "media_type must be 'image' or 'video'.")
    if not urls:
        if media_type is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "media_type requires media.")
        return
    if media_type is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "media_type is required when attaching media.")
    if media_type == "video" and len(urls) != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A video post must contain one video.")
    allowed = IMAGE_EXTENSIONS if media_type == "image" else VIDEO_EXTENSIONS
    for url in urls:
        if not url or len(url) > 2048 or urlsplit(url).path.rsplit(".", 1)[-1].lower() not in allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid {media_type} upload URL.")
