"""
Tests for app/services/storage/upload_service.py.

The database lifecycle of a media asset is tested from pending to attached to
deleted. S3 URL generation, existence checks, and deletion are the only mocked
boundaries.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset
from app.services.storage.upload_service import (
    attach_media_asset,
    confirm_upload,
    release_media_asset,
    request_upload,
)
from tests.conftest import make_org_with_owner


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _make_asset(
    db_session: AsyncSession,
    organization_id: str,
    *,
    object_key: str = "avatars/org/profile.png",
    status: str = "pending",
) -> MediaAsset:
    asset = MediaAsset(
        organization_id=organization_id,
        domain="avatars",
        object_key=object_key,
        status=status,
    )
    db_session.add(asset)
    await db_session.flush()
    return asset


# ── Request upload ───────────────────────────────────────────────────────────

class TestRequestUpload:

    @pytest.mark.asyncio
    async def test_returns_provider_urls_and_creates_pending_asset(
        self, db_session: AsyncSession, monkeypatch
    ):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        monkeypatch.setattr(
            "app.services.storage.upload_service.build_object_key",
            lambda **_: "avatars/org/profile.png",
        )
        monkeypatch.setattr(
            "app.services.storage.upload_service.generate_presigned_upload_url",
            lambda **_: "https://upload.test/profile",
        )
        monkeypatch.setattr(
            "app.services.storage.upload_service.get_object_url",
            lambda **_: "https://assets.test/profile.png",
        )

        result = await request_upload(
            session=db_session,
            domain="avatars",
            organization_id=org.id,
            content_type="image/png",
        )

        assert result == {
            "upload_url": "https://upload.test/profile",
            "object_key": "avatars/org/profile.png",
            "media_url": "https://assets.test/profile.png",
            "content_type": "image/png",
        }
        asset = (
            await db_session.execute(
                select(MediaAsset).where(
                    MediaAsset.object_key == "avatars/org/profile.png"
                )
            )
        ).scalar_one()
        assert asset.organization_id == org.id
        assert asset.domain == "avatars"
        assert asset.status == "pending"

    @pytest.mark.asyncio
    async def test_unsupported_type_fails_before_provider_call(
        self, db_session: AsyncSession, monkeypatch
    ):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        provider = Mock()
        monkeypatch.setattr(
            "app.services.storage.upload_service.generate_presigned_upload_url",
            provider,
        )

        with pytest.raises(HTTPException) as exc:
            await request_upload(
                session=db_session,
                domain="avatars",
                organization_id=org.id,
                content_type="application/pdf",
            )

        assert exc.value.status_code == 400
        provider.assert_not_called()


# ── Confirm upload ───────────────────────────────────────────────────────────

class TestConfirmUpload:

    def test_returns_provider_existence_result(self, monkeypatch):
        exists = Mock(return_value=True)
        monkeypatch.setattr("app.services.storage.upload_service.object_exists", exists)

        assert confirm_upload(object_key="avatars/org/profile.png") is True
        exists.assert_called_once_with(object_key="avatars/org/profile.png")


# ── Attach asset ─────────────────────────────────────────────────────────────

class TestAttachMediaAsset:

    @pytest.mark.asyncio
    async def test_marks_pending_asset_attached(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        asset = await _make_asset(db_session, org.id)

        await attach_media_asset(
            session=db_session,
            object_key=asset.object_key,
            attached_to_type="user",
            attached_to_id="user-1",
        )

        assert asset.status == "attached"
        assert asset.attached_to_type == "user"
        assert asset.attached_to_id == "user-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("object_key", ["", "missing/key.png"])
    async def test_absent_asset_is_a_safe_noop(
        self, db_session: AsyncSession, object_key: str
    ):
        await attach_media_asset(
            session=db_session,
            object_key=object_key,
            attached_to_type="post",
            attached_to_id="post-1",
        )


# ── Release asset ────────────────────────────────────────────────────────────

class TestReleaseMediaAsset:

    @pytest.mark.asyncio
    async def test_deletes_object_and_marks_asset_deleted(
        self, db_session: AsyncSession, monkeypatch
    ):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        asset = await _make_asset(db_session, org.id, status="attached")
        delete_object = Mock()
        monkeypatch.setattr(
            "app.services.storage.upload_service.delete_object", delete_object
        )

        await release_media_asset(session=db_session, object_key=asset.object_key)

        delete_object.assert_called_once_with(object_key=asset.object_key)
        assert asset.status == "deleted"

    @pytest.mark.asyncio
    async def test_already_deleted_asset_does_not_delete_twice(
        self, db_session: AsyncSession, monkeypatch
    ):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        asset = await _make_asset(db_session, org.id, status="deleted")
        delete_object = Mock()
        monkeypatch.setattr(
            "app.services.storage.upload_service.delete_object", delete_object
        )

        await release_media_asset(session=db_session, object_key=asset.object_key)

        delete_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_asset_is_a_safe_noop(
        self, db_session: AsyncSession, monkeypatch
    ):
        delete_object = Mock()
        monkeypatch.setattr(
            "app.services.storage.upload_service.delete_object", delete_object
        )

        await release_media_asset(
            session=db_session, object_key="avatars/missing/profile.png"
        )

        delete_object.assert_not_called()
