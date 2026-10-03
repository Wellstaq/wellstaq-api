"""
HTTP route tests for app/routers/storages.py.

The upload-url endpoint is tested with real authentication, request
validation, tenant-scoped MediaAsset persistence, and transaction ownership.
Only the object-storage provider boundary is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset
from tests.conftest import authenticate_user, make_org_with_owner


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _authenticated_org(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    headers, _ = await authenticate_user(
        db_session,
        owner,
        device_fingerprint=f"storage-router-{owner.id}",
    )
    return org, owner, headers


def _mock_storage_provider(monkeypatch):
    monkeypatch.setattr(
        "app.services.storage.upload_service.generate_presigned_upload_url",
        lambda *, object_key, content_type: f"https://upload.test/{object_key}?type={content_type}",
    )
    monkeypatch.setattr(
        "app.services.storage.upload_service.get_object_url",
        lambda *, object_key: f"https://assets.test/{object_key}",
    )


# ── Authentication and validation ────────────────────────────────────────────

class TestRequestUploadUrlValidation:

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.post(
            "/storage/upload-url",
            json={"domain": "avatars", "content_type": "image/png"},
        )

        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_rejects_unknown_domain(
        self, client, db_session: AsyncSession
    ):
        _, _, headers = await _authenticated_org(db_session)

        response = await client.post(
            "/storage/upload-url",
            headers=headers,
            json={"domain": "documents", "content_type": "image/png"},
        )

        assert response.status_code == 400
        assert "domain must be one of" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_unsupported_content_type(
        self, client, db_session: AsyncSession
    ):
        _, _, headers = await _authenticated_org(db_session)

        response = await client.post(
            "/storage/upload-url",
            headers=headers,
            json={"domain": "avatars", "content_type": "application/pdf"},
        )

        assert response.status_code == 400
        assert "Unsupported content_type" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_requires_both_request_fields(
        self, client, db_session: AsyncSession
    ):
        _, _, headers = await _authenticated_org(db_session)

        response = await client.post(
            "/storage/upload-url", headers=headers, json={"domain": "avatars"}
        )

        assert response.status_code == 422


# ── Successful upload request ────────────────────────────────────────────────

class TestRequestUploadUrlSuccess:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "domain",
        ["posts", "stories", "chat", "avatars", "challenges", "events", "clubs"],
    )
    async def test_accepts_every_supported_domain(
        self, client, db_session: AsyncSession, monkeypatch, domain: str
    ):
        org, _, headers = await _authenticated_org(db_session)
        _mock_storage_provider(monkeypatch)

        response = await client.post(
            "/storage/upload-url",
            headers=headers,
            json={"domain": domain, "content_type": "image/png"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["content_type"] == "image/png"
        assert body["object_key"].startswith(f"{domain}/{org.id}/")
        assert body["object_key"].endswith(".png")
        assert body["upload_url"].startswith("https://upload.test/")
        assert body["media_url"].startswith("https://assets.test/")

    @pytest.mark.asyncio
    async def test_persists_pending_asset_for_requesting_organization(
        self, client, db_session: AsyncSession, monkeypatch
    ):
        org, _, headers = await _authenticated_org(db_session)
        _mock_storage_provider(monkeypatch)

        response = await client.post(
            "/storage/upload-url",
            headers=headers,
            json={"domain": "avatars", "content_type": "image/webp"},
        )

        assert response.status_code == 200
        asset = (
            await db_session.execute(
                select(MediaAsset).where(
                    MediaAsset.object_key == response.json()["object_key"]
                )
            )
        ).scalar_one()
        assert asset.organization_id == org.id
        assert asset.domain == "avatars"
        assert asset.status == "pending"

    @pytest.mark.asyncio
    async def test_route_owns_successful_transaction_commit(
        self, client, db_session: AsyncSession, monkeypatch
    ):
        _, _, headers = await _authenticated_org(db_session)
        _mock_storage_provider(monkeypatch)
        real_commit = db_session.commit
        commit_spy = AsyncMock(side_effect=real_commit)
        monkeypatch.setattr(db_session, "commit", commit_spy)

        response = await client.post(
            "/storage/upload-url",
            headers=headers,
            json={"domain": "events", "content_type": "image/jpeg"},
        )

        assert response.status_code == 200
        commit_spy.assert_awaited_once()
