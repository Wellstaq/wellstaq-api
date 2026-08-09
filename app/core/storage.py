"""
app/core/storage.py

Thin wrapper around boto3's S3 client. No business logic — just the
low-level presigned URL generation and object existence checks. Domain-
specific logic (validating content types per feature, building the
final object key structure) lives in app/services/storage/.
"""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

_s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.STORAGE_ACCESS_KEY_ID,
    aws_secret_access_key=settings.STORAGE_SECRET_ACCESS_KEY,
    region_name=settings.STORAGE_REGION,
)


def generate_presigned_upload_url(*, object_key: str, content_type: str) -> str:
    """Returns a presigned PUT URL valid for S3_PRESIGNED_URL_EXPIRY_SECONDS.
    The client PUTs the raw file bytes directly to this URL with the
    same Content-Type header specified here — a mismatched header will
    cause S3 to reject the upload (this is intentional: it prevents a
    client from uploading a file type different from what was declared)."""
    return _s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.STORAGE_BUCKET_NAME, "Key": object_key, "ContentType": content_type},
        ExpiresIn=settings.PRESIGNED_URL_EXPIRY_SECONDS,
    )


def object_exists(*, object_key: str) -> bool:
    """HEAD-checks whether an object was actually uploaded — used to
    verify a client's 'upload complete' claim before trusting it."""
    try:
        _s3_client.head_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=object_key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        logger.error("Unexpected S3 error checking object existence", exc_info=True, extra={"object_key": object_key})
        raise

def get_object_url(*, object_key: str) -> str:
    """Public HTTPS URL for a stored object, served via CloudFront.
    The S3 bucket itself is private — CloudFront (with Origin Access
    Control) is the only thing allowed to read from it."""
    return f"https://{settings.STORAGE_CDN_DOMAIN}/{object_key}"


def delete_object(*, object_key: str) -> None:
    """Used by the story purge job and any future 'delete media on
    content delete' flow."""
    _s3_client.delete_object(Bucket=settings.STORAGE_BUCKET_NAME, Key=object_key)