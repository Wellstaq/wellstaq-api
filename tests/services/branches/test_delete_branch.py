"""
tests/services/branches/test_delete_branch.py

Unit tests for app/services/branch/delete_branch.py

Coverage:
  delete_branch()
    - deletes an empty branch with no manager successfully
    - branch row is gone after deletion
    - raises 404 when branch not found
    - raises 404 when branch belongs to a different org
    - raises 409 when branch has an assigned manager
    - raises 409 when branch has members and no target provided
    - raises 409 when only target_branch_id provided (target_department_id missing)
    - raises 409 when only target_department_id provided (target_branch_id missing)
    - error message includes member count when blocking deletion
    - raises 400 when target_branch_id equals branch_id
    - raises 404 when target_branch_id not found
    - raises 404 when target_branch_id belongs to a different org
    - transfers all members to target branch before deletion
    - transferred members have correct branch_id after transfer
    - branch row is deleted after successful member transfer
    - member transfer does not affect users from other orgs
    - empty branch with a target provided deletes cleanly
    - does not affect users already in the target branch
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
) -> Branch:
    branch = Branch(
        organization_id=organization_id,
        name=name,
        manager_id=manager_id,
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
    target_branch_id: str | None = None,
    target_department_id: str | None = None,
) -> None:
    await delete_branch(
        session=db_session,
        organization_id=organization_id,
        branch_id=branch_id,
        target_branch_id=target_branch_id,
        target_department_id=target_department_id,
    )


# ── Happy path: empty branch ───────────────────────────────────────────────────

class TestDeleteEmptyBranch:

    @pytest.mark.asyncio
    async def test_deletes_empty_branch_with_no_manager(
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

    @pytest.mark.asyncio
    async def test_empty_branch_with_targets_provided_deletes_cleanly(
        self, db_session: AsyncSession
    ):
        """Providing targets when there are no members is harmless — the
        service should skip the transfer step and delete cleanly."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Source")
        target_branch = await _make_branch(db_session, org.id, name="Target")
        target_dept = await _make_department(db_session, org.id, target_branch.id)

        await _call(
            db_session, org.id, branch.id,
            target_branch_id=target_branch.id,
            target_department_id=target_dept.id,
        )

        result = await db_session.execute(
            select(Branch).where(Branch.id == branch.id)
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
    async def test_manager_guard_fires_before_member_check(
        self, db_session: AsyncSession
    ):
        """Manager check fires even when there are no members, confirming
        it is not short-circuited by member_count == 0."""
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409
        assert "manager" in exc_info.value.detail.lower()


# ── Member transfer guard ──────────────────────────────────────────────────────

class TestDeleteBranchMemberGuard:

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_has_members_and_no_targets(
        self, db_session: AsyncSession
    ):
        """Both target_branch_id and target_department_id are required when
        members exist — omitting both raises 409."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_only_target_branch_provided(
        self, db_session: AsyncSession
    ):
        """target_department_id is also required — a user transferred to a
        new branch must be placed in a department there."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Source")
        target = await _make_branch(db_session, org.id, name="Target")
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(
                db_session, org.id, branch.id,
                target_branch_id=target.id,
                target_department_id=None,   # missing
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_only_target_department_provided(
        self, db_session: AsyncSession
    ):
        """target_branch_id is also required — can't assign a department
        without knowing the destination branch."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Source")
        target = await _make_branch(db_session, org.id, name="Target")
        target_dept = await _make_department(db_session, org.id, target.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(
                db_session, org.id, branch.id,
                target_branch_id=None,          # missing
                target_department_id=target_dept.id,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_error_message_includes_member_count(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")
        await _make_user_in_branch(db_session, org.id, branch.id, "m2@example.com")
        await _make_user_in_branch(db_session, org.id, branch.id, "m3@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org.id, branch.id)

        assert "3" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_400_when_target_branch_id_equals_source(
        self, db_session: AsyncSession
    ):
        """Passing a dummy target_department_id gets past the 'provide both'
        check so the 400 (target == source) validation is reached."""
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(
                db_session, org.id, branch.id,
                target_branch_id=branch.id,
                target_department_id="00000000-0000-0000-0000-000000000099",
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_404_when_target_branch_not_found(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await _call(
                db_session, org.id, branch.id,
                target_branch_id="00000000-0000-0000-0000-000000000000",
                target_department_id="00000000-0000-0000-0000-000000000099",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_target_branch_belongs_to_different_org(
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
        branch = await _make_branch(db_session, org1.id, name="Source")
        await _make_user_in_branch(db_session, org1.id, branch.id, "m1@example.com")
        cross_org_target = await _make_branch(db_session, org2.id, name="Other")

        with pytest.raises(HTTPException) as exc_info:
            await _call(
                db_session, org1.id, branch.id,
                target_branch_id=cross_org_target.id,
                target_department_id="00000000-0000-0000-0000-000000000099",
            )

        assert exc_info.value.status_code == 404


# ── Member transfer ────────────────────────────────────────────────────────────

class TestDeleteBranchWithTransfer:

    @pytest.mark.asyncio
    async def test_transfers_all_members_to_target_branch(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        source = await _make_branch(db_session, org.id, name="Source")
        target = await _make_branch(db_session, org.id, name="Target")
        target_dept = await _make_department(db_session, org.id, target.id)

        u1 = await _make_user_in_branch(db_session, org.id, source.id, "m1@example.com")
        u2 = await _make_user_in_branch(db_session, org.id, source.id, "m2@example.com")

        await _call(
            db_session, org.id, source.id,
            target_branch_id=target.id,
            target_department_id=target_dept.id,
        )

        await db_session.refresh(u1)
        await db_session.refresh(u2)

        assert u1.branch_id == target.id
        assert u2.branch_id == target.id

    @pytest.mark.asyncio
    async def test_transferred_members_have_correct_branch_id(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        source = await _make_branch(db_session, org.id, name="Source")
        target = await _make_branch(db_session, org.id, name="Target")
        target_dept = await _make_department(db_session, org.id, target.id)

        user = await _make_user_in_branch(
            db_session, org.id, source.id, "m1@example.com"
        )
        user_id = user.id

        await _call(
            db_session, org.id, source.id,
            target_branch_id=target.id,
            target_department_id=target_dept.id,
        )

        result = await db_session.execute(
            select(User).where(User.id == user_id)
        )
        fetched = result.scalar_one()
        assert fetched.branch_id == target.id

    @pytest.mark.asyncio
    async def test_source_branch_is_deleted_after_transfer(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        source = await _make_branch(db_session, org.id, name="Source")
        target = await _make_branch(db_session, org.id, name="Target")
        target_dept = await _make_department(db_session, org.id, target.id)
        source_id = source.id

        await _make_user_in_branch(db_session, org.id, source.id, "m1@example.com")

        await _call(
            db_session, org.id, source_id,
            target_branch_id=target.id,
            target_department_id=target_dept.id,
        )

        result = await db_session.execute(
            select(Branch).where(Branch.id == source_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_does_not_affect_users_already_in_target_branch(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        source = await _make_branch(db_session, org.id, name="Source")
        target = await _make_branch(db_session, org.id, name="Target")
        target_dept = await _make_department(db_session, org.id, target.id)

        await _make_user_in_branch(db_session, org.id, source.id, "source@example.com")
        pre_existing = await _make_user_in_branch(
            db_session, org.id, target.id, "target@example.com"
        )
        pre_existing_id = pre_existing.id

        await _call(
            db_session, org.id, source.id,
            target_branch_id=target.id,
            target_department_id=target_dept.id,
        )

        result = await db_session.execute(
            select(User).where(User.id == pre_existing_id)
        )
        fetched = result.scalar_one()
        assert fetched.branch_id == target.id

    @pytest.mark.asyncio
    async def test_transfer_does_not_affect_users_from_other_orgs(
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

        source = await _make_branch(db_session, org1.id, name="Source")
        target = await _make_branch(db_session, org1.id, name="Target")
        target_dept = await _make_department(db_session, org1.id, target.id)

        # User in org2 — must never be touched
        org2_branch = await _make_branch(db_session, org2.id, name="Org2 Branch")
        org2_user = await _make_user_in_branch(
            db_session, org2.id, org2_branch.id, "org2user@example.com"
        )
        org2_user_id = org2_user.id
        original_branch_id = org2_user.branch_id

        await _make_user_in_branch(db_session, org1.id, source.id, "org1user@example.com")

        await _call(
            db_session, org1.id, source.id,
            target_branch_id=target.id,
            target_department_id=target_dept.id,
        )

        result = await db_session.execute(
            select(User).where(User.id == org2_user_id)
        )
        fetched = result.scalar_one()
        assert fetched.branch_id == original_branch_id