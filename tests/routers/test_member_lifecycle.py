from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import UserDevice
from tests.conftest import authenticate_user, make_org_with_owner, make_user_with_role


@pytest.mark.asyncio
async def test_org_admin_can_suspend_reinstate_and_anonymize_member(client, db_session):
    org, owner = await make_org_with_owner(db_session)
    admin = await make_user_with_role(
        db_session, org.id, role_name="admin", branch_id=owner.branch_id,
        email="org-admin@example.com", status="active",
    )
    member = await make_user_with_role(
        db_session, org.id, role_name="member", branch_id=owner.branch_id,
        email="member-to-delete@example.com", first_name="Member", last_name="Person", status="active",
    )
    db_session.add_all([admin, member])
    await db_session.flush()
    admin_headers, _ = await authenticate_user(
        db_session, admin,
        permissions=("member.suspend", "member.reinstate", "member.remove"),
    )
    _member_headers, _ = await authenticate_user(db_session, member, device_fingerprint="member-device")

    suspended = await client.post(f"/organizations/{org.id}/members/{member.id}/suspend", headers=admin_headers)
    assert suspended.status_code == 204
    await db_session.refresh(member)
    assert member.status == "suspended"

    reinstated = await client.post(f"/organizations/{org.id}/members/{member.id}/reinstate", headers=admin_headers)
    assert reinstated.status_code == 204
    await db_session.refresh(member)
    assert member.status == "active"

    deleted = await client.delete(f"/organizations/{org.id}/members/{member.id}", headers=admin_headers)
    assert deleted.status_code == 204
    await db_session.refresh(member)
    assert (member.first_name, member.last_name, member.status) == ("Deleted", "User", "inactive")
    devices = (await db_session.execute(select(UserDevice).where(UserDevice.user_id == member.id))).scalars().all()
    assert devices and all(device.revoked for device in devices)


@pytest.mark.asyncio
async def test_org_owner_cannot_be_deleted(client, db_session):
    org, owner = await make_org_with_owner(db_session)
    admin = await make_user_with_role(
        db_session, org.id, role_name="admin", branch_id=owner.branch_id,
        email="owner-guard-admin@example.com", status="active",
    )
    db_session.add(admin)
    await db_session.flush()
    admin_headers, _ = await authenticate_user(db_session, admin, permissions=("member.remove",))

    response = await client.delete(f"/organizations/{org.id}/members/{owner.id}", headers=admin_headers)

    assert response.status_code == 409
    assert owner.email == "owner@example.com"
