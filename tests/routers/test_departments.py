"""HTTP route tests for app/routers/departments.py."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, User
from app.models.department import DepartmentMember
from tests.conftest import (
    authenticate_user,
    get_or_create_system_role,
    make_org_with_owner,
    make_user,
)


async def _context(db_session: AsyncSession, *, role_name="super_admin", permissions=()):
    org, user = await make_org_with_owner(db_session, user_kwargs={"role_name": role_name})
    headers, _ = await authenticate_user(
        db_session,
        user,
        permissions=permissions,
        device_fingerprint=f"department-router-{user.id}",
    )
    return org, user, headers


async def _create(client, org_id, branch_id, headers, name):
    response = await client.post(
        f"/organizations/{org_id}/departments",
        headers=headers,
        json={
            "name": name,
            "description": f"{name} department",
            "avatar_initial": name[:2].upper(),
            "branch_id": branch_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestDepartmentRoutes:
    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/departments"
        )
        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_complete_department_management_workflow(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _context(
            db_session,
            permissions=(
                "department.create",
                "department.update",
                "department.delete",
                "member.department.assign",
            ),
        )
        engineering = await _create(
            client, org.id, owner.branch_id, headers, "Engineering"
        )
        product = await _create(client, org.id, owner.branch_id, headers, "Product")
        department_id = engineering["id"]

        org_list = await client.get(
            f"/organizations/{org.id}/departments", headers=headers
        )
        assert org_list.status_code == 200
        assert org_list.json()["total"] == 2

        branch_list = await client.get(
            f"/organizations/{org.id}/branches/{owner.branch_id}/departments",
            headers=headers,
        )
        assert branch_list.status_code == 200
        assert branch_list.json()["total"] == 2

        fetched = await client.get(
            f"/organizations/{org.id}/departments/{department_id}", headers=headers
        )
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Engineering"

        edited = await client.patch(
            f"/organizations/{org.id}/departments/{department_id}",
            headers=headers,
            json={"name": "Platform Engineering"},
        )
        assert edited.status_code == 200
        assert edited.json()["name"] == "Platform Engineering"

        member = make_user(
            org.id,
            branch_id=owner.branch_id,
            email="department-member@example.com",
            status="active",
        )
        db_session.add(member)
        await db_session.flush()
        await get_or_create_system_role(db_session, "team_leader")

        assigned = await client.put(
            f"/organizations/{org.id}/departments/{department_id}/members/{member.id}",
            headers=headers,
        )
        assert assigned.status_code == 200
        assert assigned.json()["department_id"] == department_id

        members = await client.get(
            f"/organizations/{org.id}/departments/{department_id}/members",
            headers=headers,
        )
        assert members.status_code == 200
        assert members.json()["total"] == 1
        assert members.json()["items"][0]["email"] == member.email

        head = await client.put(
            f"/organizations/{org.id}/departments/{department_id}/head/{member.id}",
            headers=headers,
        )
        assert head.status_code == 200
        assert head.json()["head_user_id"] == member.id

        removed_head = await client.delete(
            f"/organizations/{org.id}/departments/{department_id}/head",
            headers=headers,
        )
        assert removed_head.status_code == 200

        deleted = await client.request(
            "DELETE",
            f"/organizations/{org.id}/departments/{department_id}",
            headers=headers,
            json={"transfer_to_department_id": product["id"]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["members_transferred"] == 1
        assert (
            await db_session.execute(select(Department).where(Department.id == department_id))
        ).scalar_one_or_none() is None
        transferred = (
            await db_session.execute(
                select(DepartmentMember).where(DepartmentMember.user_id == member.id)
            )
        ).scalar_one()
        assert transferred.department_id == product["id"]

    @pytest.mark.asyncio
    async def test_member_without_permission_cannot_create_department(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _context(db_session, role_name="member")

        response = await client.post(
            f"/organizations/{org.id}/departments",
            headers=headers,
            json={"name": "Denied", "branch_id": member.branch_id},
        )

        assert response.status_code == 403
        assert "department.create" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_different_organization(self, client, db_session: AsyncSession):
        _, _, headers = await _context(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "department-other", "business_name": "Department Other"},
            user_kwargs={"email": "department-other@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/departments", headers=headers
        )

        assert response.status_code == 403
