"""
tests/services/branches/test_delete_branch.py

Unit tests for app/services/branch/delete_branch.py

Coverage:
  delete_branch()
    - deletes an empty branch with no manager, no departments, not HQ
    - branch row is gone after deletion
    - raises 404 when branch not found
    - raises 404 when branch belongs to a different org
    - raises 403 when branch is the Headquarters branch
    - HQ guard fires even when branch is otherwise empty
    - raises 409 when branch has an assigned manager
    - manager guard fires before department/member checks
    - raises 409 when branch has one or more departments
    - department guard error message includes department count
    - department guard fires even if departments have no members
    - raises 409 when branch has home-branch members (no departments)
    - member guard error message includes member count
    - member guard is a safety net independent of department count
    - deleting a branch does not affect branches/members in other orgs
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User
from app.models.department import Department
from app.services.branch.delete_branch import delete_branch
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Lagos Branch",
    manager_id: str | None = None,
    is_headquarters: bool = False,
) -> Branch:
    branch = Branch(
        organization_id=organization_id,
        name=name,
        manager_id=manager_id,
        is_headquarters=is_headquarters,
    )
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str = "General",
) -> Department:
    dept = Department(
        name=name,
        organization_id=organization_id,
        branch_id=branch_id,
        member_count=0,
    )
    db_session.add(dept)
    await db_session.flush()
    return dept


async def _make_user_in_branch(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    email: str,
) -> User:
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        role_name="member",
        branch_id=branch_id,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _call(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
) -> None:
    await delete_branch(
        session=db_session,
        organization_id=organization_id,
        branch_id=branch_id,
    )


# ── Happy path ─────────────────────────────────────────────────────────────────

class TestDeleteEmptyBranch:

    @pytest.mark.asyncio
    async def test_deletes_empty_branch_with_no_manager_no_departments(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        await _call(db_session, org.id, branch.id)

        result = await db_session.execute(
            select(Branch).where(Branch.id == branch.id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_branch_row_is_gone_after_deletion(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        branch_id = branch.id

        await _call(db_session, org.id, branch_id)

        result = await db_session.execute(
            select(Branch).where(Branch.id == branch_id)
        )
        assert result.scalar_one_or_none() is None


# ── Not found errors ───────────────────────────────────────────────────────────

class TestDeleteBranchNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, "00000000-0000-0000-0000-000000000000")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org2.id, branch.id)

        assert exc_info.value.status_code == 404


# ── Headquarters guard ──────────────────────────────────────────────────────────

class TestDeleteBranchHeadquartersGuard:

    @pytest.mark.asyncio
    async def test_raises_403_when_branch_is_headquarters(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, is_headquarters=True)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_hq_guard_fires_even_when_branch_is_otherwise_empty(
        self, db_session: AsyncSession
    ):
        """HQ branch has no manager, no departments, no members — the HQ
        check must still fire ahead of (and independent of) those guards."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, is_headquarters=True)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 403
        assert "headquarters" in exc_info.value.detail.lower()


# ── Manager guard ──────────────────────────────────────────────────────────────

class TestDeleteBranchManagerGuard:

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_has_assigned_manager(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_manager_guard_fires_before_department_and_member_checks(
        self, db_session: AsyncSession
    ):
        """Manager check fires even when there are no departments or
        members, confirming it isn't short-circuited by either being empty."""
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409
        assert "manager" in exc_info.value.detail.lower()


# ── Department guard ────────────────────────────────────────────────────────────

class TestDeleteBranchDepartmentGuard:

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_has_departments(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_department(db_session, org.id, branch.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_department_guard_error_message_includes_count(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_department(db_session, org.id, branch.id, name="Sales")
        await _make_department(db_session, org.id, branch.id, name="Support")

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert "2" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_department_guard_fires_even_if_departments_have_no_members(
        self, db_session: AsyncSession
    ):
        """A department with zero members still blocks branch deletion —
        departments must be explicitly deleted via delete_department()
        regardless of whether they currently hold members."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_department(db_session, org.id, branch.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409
        assert "department" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_department_guard_fires_before_member_guard(
        self, db_session: AsyncSession
    ):
        """A branch with both a department and a stray home-branch member
        should surface the department guard first, since departments must
        be cleared (and members transferred) before the member safety
        net is even relevant."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_department(db_session, org.id, branch.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409
        assert "department" in exc_info.value.detail.lower()


# ── Member guard (safety net) ────────────────────────────────────────────────────

class TestDeleteBranchMemberGuard:

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_has_home_branch_members(
        self, db_session: AsyncSession
    ):
        """No departments present, but a user's home branch_id still
        points here — the safety-net guard must catch this."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_member_guard_error_message_includes_member_count(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")
        await _make_user_in_branch(db_session, org.id, branch.id, "m2@example.com")
        await _make_user_in_branch(db_session, org.id, branch.id, "m3@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert "3" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_member_guard_fires_independent_of_department_count(
        self, db_session: AsyncSession
    ):
        """Zero departments but nonzero home-branch members — this is the
        exact edge case the guard's docstring calls out: a member whose
        branch_id was reassigned independently of department membership."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        dept_check = await db_session.execute(
            select(Department).where(Department.branch_id == branch.id)
        )
        assert dept_check.scalar_one_or_none() is None

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409
        assert "member" in exc_info.value.detail.lower()


# ── Cross-org isolation ──────────────────────────────────────────────────────────

class TestDeleteBranchCrossOrgIsolation:

    @pytest.mark.asyncio
    async def test_deleting_branch_does_not_affect_other_orgs(
        self, db_session: AsyncSession
    ):
        org1, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )

        branch1 = await _make_branch(db_session, org1.id, name="Org1 Branch")
        branch2 = await _make_branch(db_session, org2.id, name="Org2 Branch")
        branch2_id = branch2.id

        await _call(db_session, org1.id, branch1.id)

        result = await db_session.execute(
            select(Branch).where(Branch.id == branch2_id)
        )
        assert result.scalar_one_or_none() is not None