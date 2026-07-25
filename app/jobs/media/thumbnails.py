"""
app/jobs/media/thumbnails.py
"""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image

from app.core.config import settings
from app.core.storage import _s3_client

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = (400, 400)


async def resize_thumbnail(ctx: dict[str, Any], object_key: str) -> None:
    thumb_key = object_key.rsplit(".", 1)[0] + "_thumb." + object_key.rsplit(".", 1)[1]

    response = _s3_client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
    image_bytes = response["Body"].read()

    with Image.open(io.BytesIO(image_bytes)) as img:
        img.thumbnail(THUMBNAIL_SIZE)
        buffer = io.BytesIO()
        img.save(buffer, format=img.format or "JPEG")
        buffer.seek(0)

        _s3_client.put_object(
            Bucket=settings.S3_BUCKET_NAME, Key=thumb_key, Body=buffer,
            ContentType=response.get("ContentType", "image/jpeg"),
        )

    logger.info("Thumbnail generated", extra={"original_key": object_key, "thumbnail_key": thumb_key})