"""
HTTP route tests for app/routers/branches.py.

Every branch endpoint is exercised through FastAPI's ASGI client with the
real database dependency, authentication dependency, subscription guard,
permission resolution, service layer, transaction boundary, and response
schema.

Coverage groups:
- authentication and tenant isolation shared by branch routes;
- create/list/get/update/delete success and failure behavior;
- member listing and atomic branch/department transfer;
- manager assignment/revocation and persisted role assignments;
- request validation and response allow-listing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Role, User, UserRoleAssignment
from app.models.department import Department, DepartmentMember
from tests.conftest import authenticate_user, make_org_with_owner, make_user


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _authorized_org(
    db_session: AsyncSession,
    *,
    permissions: tuple[str, ...] = (),
):
    org, owner = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session,
        owner,
        permissions=permissions,
        device_fingerprint=f"branch-router-{owner.id}",
    )
    return org, owner, headers


async def _make_branch(
    db_session: AsyncSession,
    organization_id: str,
    *,
    name: str = "Lagos Branch",
    created_by: str | None = None,
) -> Branch:
    branch = Branch(
        organization_id=organization_id,
        name=name,
        created_by=created_by,
    )
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    *,
    name: str,
    created_by: str,
) -> Department:
    department = Department(
        organization_id=organization_id,
        branch_id=branch_id,
        name=name,
        created_by=created_by,
    )
    db_session.add(department)
    await db_session.flush()
    return department


async def _make_branch_role(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str,
) -> Role:
    role = Role(
        name=name,
        organization_id=organization_id,
        branch_id=branch_id,
        is_system=False,
        is_unique=name == "branch_manager",
        unique_scope="branch" if name == "branch_manager" else None,
    )
    db_session.add(role)
    await db_session.flush()
    return role


# ── Shared authentication and tenant isolation ───────────────────────────────

class TestBranchRouteAccess:

    @pytest.mark.asyncio
    async def test_list_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/branches"
        )

        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_list_rejects_a_different_organization(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authorized_org(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "other-branch-org", "business_name": "Other Branch Org"},
            user_kwargs={"email": "other-branch-owner@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/branches", headers=headers
        )

        assert response.status_code == 403
        assert "organization" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_mutation_rejects_missing_permission(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authorized_org(db_session)

        response = await client.post(
            f"/organizations/{org.id}/branches",
            headers=headers,
            json={"name": "Abuja"},
        )

        assert response.status_code == 403
        assert "branch.create" in response.json()["detail"]


# ── Create ───────────────────────────────────────────────────────────────────

class TestCreateBranchRoute:

    @pytest.mark.asyncio
    async def test_creates_branch_and_persists_actor_as_creator(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/branches",
            headers=headers,
            json={"name": "  Abuja Branch  "},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Abuja Branch"
        assert body["organization_id"] == org.id
        assert body["created_by"] == owner.id
        branch = (
            await db_session.execute(select(Branch).where(Branch.id == body["id"]))
        ).scalar_one()
        assert branch.created_by == owner.id

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_conflict(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.create",)
        )
        await _make_branch(db_session, org.id, name="Abuja", created_by=owner.id)

        response = await client.post(
            f"/organizations/{org.id}/branches",
            headers=headers,
            json={"name": "ABUJA"},
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_empty_name_is_rejected_by_request_schema(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authorized_org(
            db_session, permissions=("branch.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/branches", headers=headers, json={"name": ""}
        )

        assert response.status_code == 422


# ── List and get ──────────────────────────────────────────────────────────────

class TestReadBranchRoutes:

    @pytest.mark.asyncio
    async def test_list_is_paginated_sorted_and_tenant_scoped(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        await _make_branch(db_session, org.id, name="Zaria", created_by=owner.id)
        await _make_branch(db_session, org.id, name="Abuja", created_by=owner.id)

        response = await client.get(
            f"/organizations/{org.id}/branches?offset=1&limit=2", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["offset"] == 1
        assert body["limit"] == 2
        assert [item["name"] for item in body["items"]] == ["Headquarters", "Zaria"]

    @pytest.mark.asyncio
    async def test_list_rejects_invalid_pagination(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authorized_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/branches?limit=0", headers=headers
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_returns_requested_branch(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        branch = await _make_branch(db_session, org.id, created_by=owner.id)

        response = await client.get(
            f"/organizations/{org.id}/branches/{branch.id}", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["id"] == branch.id
        assert response.json()["name"] == "Lagos Branch"

    @pytest.mark.asyncio
    async def test_get_returns_not_found_for_unknown_branch(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authorized_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/branches/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )

        assert response.status_code == 404


# ── Update and delete ─────────────────────────────────────────────────────────

class TestUpdateBranchRoute:

    @pytest.mark.asyncio
    async def test_update_requires_branch_update_and_persists_name(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.update",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)

        response = await client.patch(
            f"/organizations/{org.id}/branches/{branch.id}",
            headers=headers,
            json={"name": "Lagos Operations"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Lagos Operations"
        await db_session.refresh(branch)
        assert branch.name == "Lagos Operations"

    @pytest.mark.asyncio
    async def test_update_does_not_accept_transfer_permission_instead(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("member.branch.assign",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)

        response = await client.patch(
            f"/organizations/{org.id}/branches/{branch.id}",
            headers=headers,
            json={"name": "Should Not Change"},
        )

        assert response.status_code == 403
        await db_session.refresh(branch)
        assert branch.name == "Lagos Branch"

    @pytest.mark.asyncio
    async def test_headquarters_cannot_be_renamed(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.update",)
        )
        hq = (
            await db_session.execute(
                select(Branch).where(Branch.id == owner.branch_id)
            )
        ).scalar_one()
        hq.is_headquarters = True
        await db_session.flush()

        response = await client.patch(
            f"/organizations/{org.id}/branches/{hq.id}",
            headers=headers,
            json={"name": "Renamed HQ"},
        )

        assert response.status_code == 403
        assert "headquarters" in response.json()["detail"].lower()


class TestDeleteBranchRoute:

    @pytest.mark.asyncio
    async def test_deletes_an_empty_non_headquarters_branch(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.delete",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)
        branch_id = branch.id

        response = await client.delete(
            f"/organizations/{org.id}/branches/{branch_id}", headers=headers
        )

        assert response.status_code == 204
        assert (
            await db_session.execute(select(Branch).where(Branch.id == branch_id))
        ).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_does_not_delete_branch_that_still_has_members(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.delete",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)
        member = make_user(org.id, email="branch-member@example.com", branch_id=branch.id)
        db_session.add(member)
        await db_session.flush()

        response = await client.delete(
            f"/organizations/{org.id}/branches/{branch.id}", headers=headers
        )

        assert response.status_code == 409
        assert "member" in response.json()["detail"].lower()


# ── Members ──────────────────────────────────────────────────────────────────

class TestBranchMemberRoutes:

    @pytest.mark.asyncio
    async def test_list_members_returns_allow_list_without_password(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        member = make_user(
            org.id,
            email="listed-member@example.com",
            first_name="Bola",
            branch_id=owner.branch_id,
            status="active",
        )
        db_session.add(member)
        await db_session.flush()

        response = await client.get(
            f"/organizations/{org.id}/branches/{owner.branch_id}/members",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        listed = next(item for item in body["items"] if item["id"] == member.id)
        assert listed["email"] == "listed-member@example.com"
        assert "hashed_password" not in listed
        assert "role_id" not in listed

    @pytest.mark.asyncio
    async def test_transfer_uses_member_branch_assign_and_moves_department_atomically(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("member.branch.assign",)
        )
        source_department = await _make_department(
            db_session,
            org.id,
            owner.branch_id,
            name="Source Department",
            created_by=owner.id,
        )
        destination = await _make_branch(
            db_session, org.id, name="Destination", created_by=owner.id
        )
        destination_department = await _make_department(
            db_session,
            org.id,
            destination.id,
            name="Destination Department",
            created_by=owner.id,
        )
        member = make_user(
            org.id, email="transfer-member@example.com", branch_id=owner.branch_id
        )
        db_session.add(member)
        await db_session.flush()
        source_role = Role(
            name="team_leader", organization_id=org.id, branch_id=owner.branch_id,
            is_system=False,
        )
        destination_role = Role(
            name="team_leader", organization_id=org.id, branch_id=destination.id,
            is_system=False,
        )
        db_session.add_all([source_role, destination_role])
        await db_session.flush()
        db_session.add(UserRoleAssignment(
            user_id=member.id, role_id=source_role.id, organization_id=org.id,
            branch_id=owner.branch_id,
        ))
        await db_session.flush()
        db_session.add(DepartmentMember(
            department_id=source_department.id, user_id=member.id
        ))
        source_department.member_count = 1
        await db_session.flush()

        response = await client.put(
            f"/organizations/{org.id}/branches/{destination.id}/members/{member.id}",
            headers=headers,
            json={"department_id": destination_department.id},
        )

        assert response.status_code == 200
        await db_session.refresh(member)
        membership = (
            await db_session.execute(
                select(DepartmentMember).where(DepartmentMember.user_id == member.id)
            )
        ).scalar_one()
        assert member.branch_id == destination.id
        assert membership.department_id == destination_department.id
        assert source_department.member_count == 0
        assert destination_department.member_count == 1
        assignment = (await db_session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == member.id)
        )).scalar_one()
        assert assignment.branch_id == destination.id
        assert assignment.role_id == destination_role.id

    @pytest.mark.asyncio
    async def test_transfer_does_not_accept_branch_update_instead(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.update",)
        )
        destination = await _make_branch(db_session, org.id, created_by=owner.id)

        response = await client.put(
            f"/organizations/{org.id}/branches/{destination.id}/members/{owner.id}",
            headers=headers,
            json={"department_id": "00000000-0000-0000-0000-000000000000"},
        )

        assert response.status_code == 403


# ── Manager ──────────────────────────────────────────────────────────────────

class TestBranchManagerRoutes:

    @pytest.mark.asyncio
    async def test_assign_and_unassign_manager_updates_role_assignment(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.manager.assign",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)
        manager = make_user(
            org.id,
            email="new-manager@example.com",
            branch_id=branch.id,
            status="active",
        )
        db_session.add(manager)
        await db_session.flush()
        manager_role = await _make_branch_role(
            db_session, org.id, branch.id, "branch_manager"
        )
        member_role = await _make_branch_role(
            db_session, org.id, branch.id, "member"
        )

        assigned = await client.put(
            f"/organizations/{org.id}/branches/{branch.id}/manager",
            headers=headers,
            json={"manager_id": manager.id},
        )

        assert assigned.status_code == 200
        assert assigned.json()["manager_id"] == manager.id
        assignment = (
            await db_session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == manager.id,
                    UserRoleAssignment.branch_id == branch.id,
                )
            )
        ).scalar_one()
        assert assignment.role_id == manager_role.id

        unassigned = await client.delete(
            f"/organizations/{org.id}/branches/{branch.id}/manager",
            headers=headers,
        )

        assert unassigned.status_code == 200
        assert unassigned.json()["manager_id"] is None
        replacement = (
            await db_session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == manager.id,
                    UserRoleAssignment.branch_id == branch.id,
                )
            )
        ).scalar_one()
        assert replacement.role_id == member_role.id

    @pytest.mark.asyncio
    async def test_manager_can_manage_target_branch_without_changing_home_branch(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.manager.assign",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)
        await _make_branch_role(db_session, org.id, branch.id, "branch_manager")
        manager = make_user(
            org.id, email="wrong-branch-manager@example.com", branch_id=owner.branch_id
        )
        db_session.add(manager)
        await db_session.flush()

        response = await client.put(
            f"/organizations/{org.id}/branches/{branch.id}/manager",
            headers=headers,
            json={"manager_id": manager.id},
        )

        assert response.status_code == 200
        await db_session.refresh(manager)
        assert manager.branch_id == owner.branch_id
        assignment = (await db_session.execute(select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == manager.id,
            UserRoleAssignment.branch_id == branch.id,
        ))).scalar_one()
        assert assignment.organization_id == org.id

    @pytest.mark.asyncio
    async def test_unassign_without_manager_returns_conflict(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("branch.manager.assign",)
        )
        branch = await _make_branch(db_session, org.id, created_by=owner.id)

        response = await client.delete(
            f"/organizations/{org.id}/branches/{branch.id}/manager",
            headers=headers,
        )

        assert response.status_code == 409
        assert "no manager" in response.json()["detail"].lower()
