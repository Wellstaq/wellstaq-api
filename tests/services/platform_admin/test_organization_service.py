"""Service tests for app/services/platform_admin/organization_service.py."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.platform_admin import organization_service
from tests.conftest import make_org_with_owner


@pytest.mark.asyncio
async def test_list_organizations_returns_summary_shape(db_session):
    org, _owner = await make_org_with_owner(db_session)
    items, total = await organization_service.list_organizations(session=db_session, q=None, status_filter=None, sort=None, offset=0, limit=20)
    assert total == 1
    assert items[0]["id"] == org.id
    assert items[0]["status"] == "active"
    assert items[0]["platform_comped"] is False


@pytest.mark.asyncio
async def test_list_organizations_filters_by_q_matching_owner_email(db_session):
    org, owner = await make_org_with_owner(db_session, user_kwargs={"email": "findme@example.com"})
    items, total = await organization_service.list_organizations(session=db_session, q="findme", status_filter=None, sort=None, offset=0, limit=20)
    assert total == 1
    assert items[0]["id"] == org.id


@pytest.mark.asyncio
async def test_suspend_organization_sets_reason_and_timestamp(db_session):
    org, _owner = await make_org_with_owner(db_session)
    result = await organization_service.suspend_organization(session=db_session, org_id=org.id, reason="Unpaid invoice for two months.")
    assert result["status"] == "suspended"
    assert result["suspension_reason"] == "Unpaid invoice for two months."
    assert result["suspended_at"] is not None
    await db_session.refresh(org)
    assert org.is_active is False


@pytest.mark.asyncio
async def test_suspend_already_suspended_organization_conflicts(db_session):
    org, _owner = await make_org_with_owner(db_session)
    await organization_service.suspend_organization(session=db_session, org_id=org.id, reason="First suspension reason.")
    with pytest.raises(HTTPException) as exc:
        await organization_service.suspend_organization(session=db_session, org_id=org.id, reason="Second suspension reason.")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reactivate_clears_suspension_state(db_session):
    org, _owner = await make_org_with_owner(db_session)
    await organization_service.suspend_organization(session=db_session, org_id=org.id, reason="Testing suspend/reactivate.")
    result = await organization_service.reactivate_organization(session=db_session, org_id=org.id)
    assert result["status"] == "active"
    assert result["suspension_reason"] is None
    assert result["suspended_at"] is None


@pytest.mark.asyncio
async def test_reactivate_non_suspended_organization_conflicts(db_session):
    org, _owner = await make_org_with_owner(db_session)
    with pytest.raises(HTTPException) as exc:
        await organization_service.reactivate_organization(session=db_session, org_id=org.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_grant_free_access_sets_platform_comped(db_session):
    org, _owner = await make_org_with_owner(db_session)
    result = await organization_service.grant_free_access(session=db_session, org_id=org.id)
    assert result["platform_comped"] is True


@pytest.mark.asyncio
async def test_grant_free_access_twice_conflicts(db_session):
    org, _owner = await make_org_with_owner(db_session)
    await organization_service.grant_free_access(session=db_session, org_id=org.id)
    with pytest.raises(HTTPException) as exc:
        await organization_service.grant_free_access(session=db_session, org_id=org.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_revoke_free_access_clears_flag(db_session):
    org, _owner = await make_org_with_owner(db_session)
    await organization_service.grant_free_access(session=db_session, org_id=org.id)
    result = await organization_service.revoke_free_access(session=db_session, org_id=org.id)
    assert result["platform_comped"] is False


@pytest.mark.asyncio
async def test_revoke_free_access_when_not_comped_conflicts(db_session):
    org, _owner = await make_org_with_owner(db_session)
    with pytest.raises(HTTPException) as exc:
        await organization_service.revoke_free_access(session=db_session, org_id=org.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_suspend_and_comp_are_independent(db_session):
    """Suspending a comped org keeps the comp; it's just also blocked
    from access until reactivated. The two levers are orthogonal."""
    org, _owner = await make_org_with_owner(db_session)
    await organization_service.grant_free_access(session=db_session, org_id=org.id)
    result = await organization_service.suspend_organization(session=db_session, org_id=org.id, reason="Policy violation investigation.")
    assert result["status"] == "suspended"
    assert result["platform_comped"] is True


@pytest.mark.asyncio
async def test_get_organization_detail_not_found(db_session):
    with pytest.raises(HTTPException) as exc:
        await organization_service.get_organization_detail(session=db_session, org_id="does-not-exist")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_search_users_matches_by_email_across_organizations(db_session):
    org, owner = await make_org_with_owner(db_session, org_kwargs={"business_name": "Acme Co", "slug": "acme-co"}, user_kwargs={"email": "findme@example.com"})
    _other_org, _other_owner = await make_org_with_owner(db_session, org_kwargs={"business_name": "Other Co", "slug": "other-co"}, user_kwargs={"email": "nomatch@example.com"})

    items, total = await organization_service.search_users(session=db_session, q="findme", offset=0, limit=20)

    assert total == 1
    assert items[0]["id"] == owner.id
    assert items[0]["organization_id"] == org.id
    assert items[0]["organization_name"] == "Acme Co"


@pytest.mark.asyncio
async def test_search_users_matches_by_name(db_session):
    _org, owner = await make_org_with_owner(db_session, user_kwargs={"first_name": "Zuri", "email": "zuri@example.com"})
    items, total = await organization_service.search_users(session=db_session, q="Zuri", offset=0, limit=20)
    assert total == 1
    assert items[0]["id"] == owner.id
