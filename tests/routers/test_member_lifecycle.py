from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Club, ClubMember, Department, DepartmentMember, User, UserDevice
from app.services.organization.org_directory import get_organization_members
from app.services.platform_admin.organization_service import list_members, search_users
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
    department = Department(name="Deletion test department", organization_id=org.id,
                            branch_id=owner.branch_id, head_user_id=member.id, member_count=1)
    club = Club(name="Deletion test club", category="fitness", organization_id=org.id,
                branch_id=owner.branch_id, leader_id=member.id, member_count=1)
    db_session.add_all([department, club])
    await db_session.flush()
    db_session.add_all([DepartmentMember(department_id=department.id, user_id=member.id),
                       ClubMember(club_id=club.id, user_id=member.id)])
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
    await db_session.refresh(department)
    await db_session.refresh(club)
    assert department.member_count == club.member_count == 0
    assert department.head_user_id is None and club.leader_id is None

    directory, _ = await get_organization_members(session=db_session, organization_id=org.id)
    assert member.id not in [item["id"] for item in directory]
    directory, _ = await list_members(session=db_session, org_id=org.id, q=None, status_filter=None, offset=0, limit=50)
    assert member.id not in [item["id"] for item in directory]
    results, _ = await search_users(session=db_session, q="Deleted", offset=0, limit=50)
    assert member.id not in [item["id"] for item in results]
    org_id, member_id, branch_id = org.id, member.id, owner.branch_id
    for action in ("suspend", "reinstate"):
        response = await client.post(f"/organizations/{org_id}/members/{member_id}/{action}", headers=admin_headers)
        assert response.status_code == 404

    # A fresh account may reuse the original identity, but gets a new ID and
    # cannot inherit the deleted account's permissions or historical records.
    replacement = await make_user_with_role(
        db_session, org_id, role_name="member", branch_id=branch_id,
        email="member-to-delete@example.com", first_name="Member", last_name="Person", status="active",
    )
    db_session.add(replacement)
    await db_session.flush()
    assert replacement.id != member_id
    assert (await db_session.execute(select(User).where(User.id == member_id))).scalar_one().first_name == "Deleted"


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
