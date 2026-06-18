"""
Object storage client (S3 or Cloudflare R2 — both speak the same
S3 API, so this code works unmodified against either; only the
.env values change).

Uses aioboto3 rather than plain boto3 deliberately: boto3's calls
are synchronous/blocking, and a blocking call inside an async route
handler stalls the whole event loop, not just that one request.
aioboto3 wraps the same API in proper async/await.

Flow for uploads (never route raw file bytes through FastAPI):
    1. Client asks our API for a presigned upload URL
    2. Our API returns a short-lived URL (see PRESIGNED_URL_EXPIRE_SECONDS)
    3. Client uploads the file bytes directly to S3/R2 using that URL
    4. Client tells our API the upload is done (object_key), and we
       store that key/URL on the relevant row (e.g. posts.media_url)

Thumbnail generation for feed images should NOT happen inline here —
trigger a background job (see app/jobs/) that downloads the original,
resizes with Pillow, and re-uploads the thumbnail. Keep this module
limited to the storage operations themselves.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aioboto3

from app.core.config import settings


def _client_kwargs() -> dict:
    """Build the kwargs aioboto3 needs, working for both AWS S3 and R2."""
    kwargs: dict = {
        "region_name": settings.STORAGE_REGION,
        "aws_access_key_id": settings.STORAGE_ACCESS_KEY_ID,
        "aws_secret_access_key": settings.STORAGE_SECRET_ACCESS_KEY,
    }
    # Only set endpoint_url for R2 / non-AWS S3-compatible providers.
    # Leaving this unset lets aioboto3 resolve the standard AWS endpoint.
    if settings.STORAGE_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.STORAGE_ENDPOINT_URL
    return kwargs


@asynccontextmanager
async def get_storage_client() -> AsyncGenerator:
    """
    Async context manager yielding an aioboto3 S3 client.

    Usage:
        async with get_storage_client() as client:
            await client.put_object(...)
    """
    session = aioboto3.Session()
    async with session.client("s3", **_client_kwargs()) as client:
        yield client


async def generate_presigned_upload_url(object_key: str, content_type: str) -> dict:
    """
    Generate a short-lived presigned URL the client can PUT a file to
    directly, bypassing our server entirely for the actual file bytes.

    Returns both the upload URL and the public URL the client/posts
    table should store once the upload completes.
    """
    async with get_storage_client() as client:
        upload_url = await client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": settings.STORAGE_BUCKET_NAME,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=settings.PRESIGNED_URL_EXPIRE_SECONDS,
        )

    public_url = (
        f"{settings.STORAGE_PUBLIC_BASE_URL.rstrip('/')}/{object_key}"
        if settings.STORAGE_PUBLIC_BASE_URL
        else f"https://{settings.STORAGE_BUCKET_NAME}.s3.amazonaws.com/{object_key}"
    )

    return {
        "upload_url": upload_url,
        "public_url": public_url,
        "object_key": object_key,
        "expires_in": settings.PRESIGNED_URL_EXPIRE_SECONDS,
    }


async def delete_object(object_key: str) -> None:
    """Delete an object from storage (e.g. when a post or avatar is removed)."""
    async with get_storage_client() as client:
        await client.delete_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=object_key)
