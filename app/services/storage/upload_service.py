"""
app/services/storage/upload_service.py

Shared presigned-upload flow, reused by posts, stories, chat, avatars,
challenges, events, and clubs. Each domain calls request_upload() to get
a presigned URL before the client uploads, then confirm_upload() to
verify the object actually landed in S3 before trusting the client's
"done" signal and using the URL in a DB row.

Services never commit — attach_media_asset/release_media_asset only
flush, so their effect is part of whatever transaction the caller
(router or outer service) eventually commits.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import generate_presigned_upload_url, get_object_url, object_exists, delete_object
from app.models import MediaAsset
from app.services.storage._helpers import ALLOWED_MEDIA_TYPES, build_object_key, content_type_to_extension


async def request_upload(
    *, session: AsyncSession, domain: str, organization_id: str, content_type: str
) -> dict:
    if content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported content_type '{content_type}'. Allowed: {ALLOWED_MEDIA_TYPES}.",
        )

    extension = content_type_to_extension(content_type)
    object_key = build_object_key(domain=domain, organization_id=organization_id, extension=extension)
    upload_url = generate_presigned_upload_url(object_key=object_key, content_type=content_type)
    final_url = get_object_url(object_key=object_key)

    # Track it as pending the moment we hand out the presigned URL —
    # this is what lets the sweep job find it later even if the client
    # never comes back to attach it to anything. Caller (router) commits.
    session.add(MediaAsset(
        organization_id=organization_id,
        domain=domain,
        object_key=object_key,
        status="pending",
    ))
    await session.flush()

    return {"upload_url": upload_url, "object_key": object_key, "media_url": final_url, "content_type": content_type}


def confirm_upload(*, object_key: str) -> bool:
    return object_exists(object_key=object_key)


async def attach_media_asset(
    *, session: AsyncSession, object_key: str, attached_to_type: str, attached_to_id: str
) -> None:
    """Call this after adding/flushing a Post/Story/Challenge/Club/etc
    row that used an uploaded image, within the same transaction. Marks
    the asset 'attached' so the pending-sweep won't touch it. Silently
    no-ops if object_key is None/empty or the row somehow doesn't exist.
    Does NOT commit — caller owns the transaction boundary."""
    if not object_key:
        return
    result = await session.execute(select(MediaAsset).where(MediaAsset.object_key == object_key))
    asset = result.scalar_one_or_none()
    if asset is None:
        return
    asset.status = "attached"
    asset.attached_to_type = attached_to_type
    asset.attached_to_id = attached_to_id
    await session.flush()


async def release_media_asset(*, session: AsyncSession, object_key: str) -> None:
    """Call this when a parent row is deleted, or when its image is
    replaced with a different upload (release the OLD object_key).
    Deletes from S3 immediately rather than waiting for the sweep job —
    the sweep job is the safety net for cases this call site missed,
    not the primary mechanism. Does NOT commit — caller owns the
    transaction boundary."""
    if not object_key:
        return
    result = await session.execute(select(MediaAsset).where(MediaAsset.object_key == object_key))
    asset = result.scalar_one_or_none()
    if asset is None or asset.status == "deleted":
        return
    delete_object(object_key=object_key)
    asset.status = "deleted"
    await session.flush()