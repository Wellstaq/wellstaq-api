"""
tests/services/departments/test_manage_head.py

Unit tests for app/services/department/manage_head.py

Coverage:
  assign_department_head()
  
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Role
from app.models.department import Department, DepartmentMember
from app.services.department.department_manage_head import (
    assign_department_head,
    remove_department_head,
)
from app.services.errors import AppError
from tests.conftest import make_org_with_owner, make_user_with_role, get_or_create_system_role


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str = "Engineering",
) -> Department:
    dept = Department(name=name, organization_id=organization_id, branch_id=branch_id, member_count=0)
    db_session.add(dept)
    await db_session.flush()
    return dept


async def _make_member(
    db_session: AsyncSession,
    department_id: str,
    organization_id: str,
    branch_id: str,
    email: str,
    role_name: str = "member",
) -> tuple[User, DepartmentMember]:
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        role_name=role_name,
        branch_id=branch_id,
    )
    db_session.add(user)
    await db_session.flush()

    membership = DepartmentMember(department_id=department_id, user_id=user.id)
    db_session.add(membership)
    await db_session.flush()
    return user, membership


async def _setup_with_head(
    db_session: AsyncSession,
) -> tuple[Department, User, DepartmentMember]:
    """Creates org, dept, member, seeds team_leader role, assigns head.
    Returns (department, head_user, membership)."""
    org, owner = await make_org_with_owner(db_session)
    dept = await _make_department(db_session, org.id, owner.branch_id)

    # seed team_leader role so assign_department_head can find it
    await get_or_create_system_role(db_session, "team_leader")

    user, membership = await _make_member(
        db_session, dept.id, org.id, owner.branch_id, "head@example.com"
    )

    dept = await assign_department_head(
        session=db_session,
        organization_id=org.id,
        department_id=dept.id,
        user_id=user.id,
    )
    return dept, user, membership


# ── assign_department_head ─────────────────────────────────────────────────────

class TestAssignDepartmentHead:

    @pytest.mark.asyncio
    async def test_assigns_head_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")
        user, _ = await _make_member(db_session, dept.id, org.id, owner.branch_id, "head@example.com")

        result = await assign_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        assert result.head_user_id == user.id

    @pytest.mark.asyncio
    async def test_sets_department_head_user_id(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")
        user, _ = await _make_member(db_session, dept.id, org.id, owner.branch_id, "head@example.com")

        await assign_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        await db_session.refresh(dept)
        assert dept.head_user_id == user.id

    @pytest.mark.asyncio
    async def test_sets_membership_role_to_lead(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")
        user, membership = await _make_member(
            db_session, dept.id, org.id, owner.branch_id, "head@example.com"
        )

        await assign_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        await db_session.refresh(membership)
        assert membership.role == "lead"

    @pytest.mark.asyncio
    async def test_assigns_team_leader_system_role_to_user(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        team_leader_role = await get_or_create_system_role(db_session, "team_leader")
        user, _ = await _make_member(db_session, dept.id, org.id, owner.branch_id, "head@example.com")

        await assign_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        await db_session.refresh(user)
        assert user.role_id == team_leader_role.id

    @pytest.mark.asyncio
    async def test_returns_updated_department(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")
        user, _ = await _make_member(db_session, dept.id, org.id, owner.branch_id, "head@example.com")

        result = await assign_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        assert isinstance(result, Department)
        assert result.id == dept.id

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(AppError) as exc_info:
            await assign_department_head(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
                user_id=owner.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_department_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        dept = await _make_department(db_session, org1.id, owner1.branch_id)

        with pytest.raises(AppError) as exc_info:
            await assign_department_head(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,
                user_id=owner1.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_user_is_not_a_department_member(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")

        # user exists in org but is NOT added to the department
        non_member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="nonmember@example.com",
            role_name="member",
            branch_id=owner.branch_id,
        )
        db_session.add(non_member)
        await db_session.flush()

        with pytest.raises(AppError) as exc_info:
            await assign_department_head(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id=non_member.id,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_found_in_org(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")

        # Insert a membership row pointing to a ghost user_id
        ghost_id = "00000000-0000-0000-0000-000000000099"

        # We can't create a real DepartmentMember with a bad FK (FK enforcement on),
        # so test this by pointing the department directly and calling service
        # with a user_id that doesn't exist in the org — we skip FK by querying org
        # We test the user-not-found path by using a different org's user
        org2, user2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="other@example.com"),
        )

        # Add user2 as a member of org1's department to bypass the membership check
        # then verify the user org check fires
        membership = DepartmentMember(department_id=dept.id, user_id=user2.id)
        db_session.add(membership)
        await db_session.flush()

        with pytest.raises(AppError) as exc_info:
            await assign_department_head(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id=user2.id,  # user2 belongs to org2, not org
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_500_when_team_leader_role_not_seeded(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)

        # deliberately do NOT seed team_leader role
        user = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="head@example.com",
            role_name="member",
            branch_id=owner.branch_id,
        )
        db_session.add(user)
        await db_session.flush()

        membership = DepartmentMember(department_id=dept.id, user_id=user.id)
        db_session.add(membership)
        await db_session.flush()

        with pytest.raises(AppError) as exc_info:
            await assign_department_head(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id=user.id,
            )

        assert exc_info.value.status_code == 500


# ── remove_department_head ─────────────────────────────────────────────────────

class TestRemoveDepartmentHead:

    @pytest.mark.asyncio
    async def test_removes_head_successfully(self, db_session: AsyncSession):
        dept, user, _ = await _setup_with_head(db_session)
        org_id = dept.organization_id

        await get_or_create_system_role(db_session, "member")

        result = await remove_department_head(
            session=db_session,
            organization_id=org_id,
            department_id=dept.id,
        )

        assert result.head_user_id is None

    @pytest.mark.asyncio
    async def test_clears_head_user_id_to_none(self, db_session: AsyncSession):
        dept, user, _ = await _setup_with_head(db_session)
        org_id = dept.organization_id
        dept_id = dept.id
        await get_or_create_system_role(db_session, "member")

        await remove_department_head(
            session=db_session,
            organization_id=org_id,
            department_id=dept_id,
        )

        await db_session.refresh(dept)
        assert dept.head_user_id is None

    @pytest.mark.asyncio
    async def test_reverts_membership_role_to_member(self, db_session: AsyncSession):
        dept, user, membership = await _setup_with_head(db_session)
        org_id = dept.organization_id
        await get_or_create_system_role(db_session, "member")

        await remove_department_head(
            session=db_session,
            organization_id=org_id,
            department_id=dept.id,
        )

        await db_session.refresh(membership)
        assert membership.role == "member"

    @pytest.mark.asyncio
    async def test_reverts_user_platform_role_when_revert_role_true(
        self, db_session: AsyncSession
    ):
        dept, user, _ = await _setup_with_head(db_session)
        org_id = dept.organization_id
        member_role = await get_or_create_system_role(db_session, "member")

        await remove_department_head(
            session=db_session,
            organization_id=org_id,
            department_id=dept.id,
            revert_role=True,
        )

        await db_session.refresh(user)
        assert user.role_id == member_role.id

    @pytest.mark.asyncio
    async def test_does_not_revert_user_platform_role_when_revert_role_false(
        self, db_session: AsyncSession
    ):
        dept, user, _ = await _setup_with_head(db_session)
        org_id = dept.organization_id

        # capture the team_leader role_id that was assigned during setup
        await db_session.refresh(user)
        team_leader_role_id = user.role_id

        await get_or_create_system_role(db_session, "member")

        await remove_department_head(
            session=db_session,
            organization_id=org_id,
            department_id=dept.id,
            revert_role=False,
        )

        await db_session.refresh(user)
        # role_id must NOT have changed — user keeps team_leader
        assert user.role_id == team_leader_role_id

    @pytest.mark.asyncio
    async def test_returns_updated_department(self, db_session: AsyncSession):
        dept, _, _ = await _setup_with_head(db_session)
        org_id = dept.organization_id
        await get_or_create_system_role(db_session, "member")

        result = await remove_department_head(
            session=db_session,
            organization_id=org_id,
            department_id=dept.id,
        )

        assert isinstance(result, Department)
        assert result.id == dept.id

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(AppError) as exc_info:
            await remove_department_head(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_department_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        dept = await _make_department(db_session, org1.id, owner1.branch_id)

        with pytest.raises(AppError) as exc_info:
            await remove_department_head(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_department_has_no_head(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)

        # head_user_id is None by default — no head set
        with pytest.raises(AppError) as exc_info:
            await remove_department_head(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_handles_gracefully_when_head_has_no_membership_row(
        self, db_session: AsyncSession
    ):
        """If the head's DepartmentMember row was already deleted (e.g.
        they were removed from the department separately), remove_department_head
        should still clear head_user_id without crashing."""
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        await get_or_create_system_role(db_session, "team_leader")
        await get_or_create_system_role(db_session, "member")

        user, membership = await _make_member(
            db_session, dept.id, org.id, owner.branch_id, "head@example.com"
        )

        await assign_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        # Manually delete the membership row to simulate the edge case
        await db_session.delete(membership)
        await db_session.flush()

        # Should not raise — member row absence is handled gracefully
        result = await remove_department_head(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            revert_role=False,
        )

        assert result.head_user_id is None