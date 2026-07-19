"""
app/jobs/media/thumbnails.py

resize_thumbnail — downloads an uploaded feed image, resizes it with
Pillow, re-uploads the thumbnail. Blocked on S3/object storage being
wired up (not yet done — see PostCreateRequest.media_url's TODO(s3) note).

Migration status: stub exists in the old app/jobs/tasks.py and needs
to be moved here (still a TODO body, not yet implemented).
"""

from typing import Any


async def resize_thumbnail(ctx: dict[str, Any], object_key: str) -> None:
    """TODO: use app.core.storage to download/upload, Pillow to resize."""
    pass