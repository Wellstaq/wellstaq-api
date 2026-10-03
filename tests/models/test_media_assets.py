"""ORM and integrity tests for app/models/media_asset.py."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset
from app.models.media_asset import DOMAINS, STATUSES
from tests.conftest import make_org_with_owner


@pytest.mark.asyncio
async def test_media_asset_starts_pending_and_unattached(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    asset = MediaAsset(
        organization_id=org.id,
        domain="avatars",
        object_key=f"organizations/{org.id}/avatars/profile.png",
    )
    db_session.add(asset)
    await db_session.flush()

    assert asset.status == "pending"
    assert asset.attached_to_type is None
    assert asset.attached_to_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", DOMAINS)
async def test_all_supported_media_domains_are_accepted(db_session: AsyncSession, domain: str):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    asset = MediaAsset(
        organization_id=org.id,
        domain=domain,
        object_key=f"organizations/{org.id}/{domain}/asset.png",
    )
    db_session.add(asset)
    await db_session.flush()

    assert asset.domain == domain


@pytest.mark.asyncio
async def test_unknown_media_domain_is_rejected(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    db_session.add(MediaAsset(
        organization_id=org.id,
        domain="unknown",
        object_key=f"organizations/{org.id}/unknown/asset.png",
    ))

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", STATUSES)
async def test_all_media_asset_statuses_are_accepted(db_session: AsyncSession, status: str):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    asset = MediaAsset(
        organization_id=org.id,
        domain="posts",
        object_key=f"organizations/{org.id}/posts/{status}.png",
        status=status,
    )
    db_session.add(asset)
    await db_session.flush()

    assert asset.status == status


@pytest.mark.asyncio
async def test_unknown_media_status_is_rejected(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    db_session.add(MediaAsset(
        organization_id=org.id,
        domain="posts",
        object_key=f"organizations/{org.id}/posts/asset.png",
        status="lost",
    ))

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_media_object_key_is_globally_unique(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    key = f"organizations/{org.id}/posts/asset.png"
    db_session.add_all([
        MediaAsset(organization_id=org.id, domain="posts", object_key=key),
        MediaAsset(organization_id=org.id, domain="posts", object_key=key),
    ])

    with pytest.raises(IntegrityError):
        await db_session.flush()
