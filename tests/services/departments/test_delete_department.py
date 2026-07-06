"""
tests/services/departments/test_delete_department.py

Unit tests for app/services/department/delete_department.py

Coverage:
  delete_department()
   
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department, DepartmentMember
from app.services.department.delete_department import delete_department
from app.services.errors import AppError
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str = "Engineering",
    member_count: int = 0,
) -> Department:
    dept = Department(
        name=name,
        organization_id=organization_id,
        branch_id=branch_id,
        member_count=member_count,
    )
    db_session.add(dept)
    await db_session.flush()
    return dept


async def _add_member(
    db_session: AsyncSession,
    department_id: str,
    organization_id: str,
    branch_id: str,
    email: str,
) -> DepartmentMember:
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        branch_id=branch_id,
        role_name="member",
    )
    db_session.add(user)
    await db_session.flush()

    membership = DepartmentMember(department_id=department_id, user_id=user.id)
    db_session.add(membership)
    await db_session.flush()
    return membership


# ── Delete empty department ────────────────────────────────────────────────────

class TestDeleteEmptyDepartment:

    @pytest.mark.asyncio
    async def test_deletes_empty_department_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)

        result = await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            transfer_to_department_id=None,
        )

        assert result["department_id"] == dept.id
        assert result["organization_id"] == org.id
        assert result["members_transferred"] == 0

    @pytest.mark.asyncio
    async def test_returns_correct_dict_shape(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)

        result = await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            transfer_to_department_id=None,
        )

        assert set(result.keys()) == {"department_id", "organization_id", "members_transferred"}

    @pytest.mark.asyncio
    async def test_department_row_is_gone_after_deletion(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        dept_id = dept.id

        await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept_id,
            transfer_to_department_id=None,
        )

        result = await db_session.execute(
            select(Department).where(Department.id == dept_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_members_transferred_is_zero_for_empty_department(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)

        result = await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            transfer_to_department_id=None,
        )

        assert result["members_transferred"] == 0


# ── Not found errors ───────────────────────────────────────────────────────────

class TestDeleteDepartmentNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
                transfer_to_department_id=None,
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
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        dept = await _make_department(db_session, org1.id, owner1.branch_id)

        # Try to delete org1's dept while claiming to be org2
        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,
                transfer_to_department_id=None,
            )

        assert exc_info.value.status_code == 404


# ── Members without transfer ───────────────────────────────────────────────────

class TestDeleteDepartmentWithMembersNoTransfer:

    @pytest.mark.asyncio
    async def test_raises_409_when_has_members_and_no_transfer(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id, member_count=1)
        await _add_member(db_session, dept.id, org.id, owner.branch_id, "member@example.com")

        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                transfer_to_department_id=None,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_409_error_message_includes_member_count(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id, member_count=3)
        await _add_member(db_session, dept.id, org.id, owner.branch_id, "m1@example.com")
        await _add_member(db_session, dept.id, org.id, owner.branch_id, "m2@example.com")
        await _add_member(db_session, dept.id, org.id, owner.branch_id, "m3@example.com")

        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                transfer_to_department_id=None,
            )

        assert "3" in str(exc_info.value)


# ── Transfer members ───────────────────────────────────────────────────────────

class TestDeleteDepartmentWithTransfer:

    @pytest.mark.asyncio
    async def test_transfers_members_to_target_atomically(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source", member_count=2)
        target = await _make_department(db_session, org.id, owner.branch_id, name="Target", member_count=0)

        m1 = await _add_member(db_session, source.id, org.id, owner.branch_id, "m1@example.com")
        m2 = await _add_member(db_session, source.id, org.id, owner.branch_id, "m2@example.com")
        user1_id = m1.user_id
        user2_id = m2.user_id

        await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=source.id,
            transfer_to_department_id=target.id,
        )

        # Both members should now be in target department
        result = await db_session.execute(
            select(DepartmentMember).where(
                DepartmentMember.department_id == target.id
            )
        )
        members = result.scalars().all()
        member_user_ids = {m.user_id for m in members}
        assert user1_id in member_user_ids
        assert user2_id in member_user_ids

    @pytest.mark.asyncio
    async def test_adjusts_target_member_count_after_transfer(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source", member_count=2)
        target = await _make_department(db_session, org.id, owner.branch_id, name="Target", member_count=5)

        await _add_member(db_session, source.id, org.id, owner.branch_id, "m1@example.com")
        await _add_member(db_session, source.id, org.id, owner.branch_id, "m2@example.com")

        await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=source.id,
            transfer_to_department_id=target.id,
        )

        await db_session.refresh(target)
        assert target.member_count == 7  # 5 original + 2 transferred

    @pytest.mark.asyncio
    async def test_returned_transferred_count_matches_actual_members(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source", member_count=3)
        target = await _make_department(db_session, org.id, owner.branch_id, name="Target", member_count=0)

        await _add_member(db_session, source.id, org.id, owner.branch_id, "m1@example.com")
        await _add_member(db_session, source.id, org.id, owner.branch_id, "m2@example.com")
        await _add_member(db_session, source.id, org.id, owner.branch_id, "m3@example.com")

        result = await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=source.id,
            transfer_to_department_id=target.id,
        )

        assert result["members_transferred"] == 3

    @pytest.mark.asyncio
    async def test_source_department_is_deleted_after_transfer(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source", member_count=1)
        target = await _make_department(db_session, org.id, owner.branch_id, name="Target", member_count=0)
        source_id = source.id

        await _add_member(db_session, source.id, org.id, owner.branch_id, "m1@example.com")

        await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=source_id,
            transfer_to_department_id=target.id,
        )

        result = await db_session.execute(
            select(Department).where(Department.id == source_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_no_member_rows_point_to_deleted_source(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source", member_count=2)
        target = await _make_department(db_session, org.id, owner.branch_id, name="Target", member_count=0)
        source_id = source.id

        await _add_member(db_session, source.id, org.id, owner.branch_id, "m1@example.com")
        await _add_member(db_session, source.id, org.id, owner.branch_id, "m2@example.com")

        await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=source_id,
            transfer_to_department_id=target.id,
        )

        result = await db_session.execute(
            select(DepartmentMember).where(
                DepartmentMember.department_id == source_id
            )
        )
        assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_transfer_ignored_when_department_is_empty(
        self, db_session: AsyncSession
    ):
        """Providing transfer_to_department_id on an empty department is fine —
        nothing to transfer, still deletes cleanly."""
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source")
        target = await _make_department(db_session, org.id, owner.branch_id, name="Target", member_count=3)

        result = await delete_department(
            session=db_session,
            organization_id=org.id,
            department_id=source.id,
            transfer_to_department_id=target.id,
        )

        assert result["members_transferred"] == 0

        # Target member_count should be unchanged
        await db_session.refresh(target)
        assert target.member_count == 3


# ── Transfer target validation ─────────────────────────────────────────────────

class TestDeleteDepartmentTransferValidation:

    @pytest.mark.asyncio
    async def test_raises_404_when_transfer_target_not_found(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        source = await _make_department(db_session, org.id, owner.branch_id, name="Source", member_count=1)
        await _add_member(db_session, source.id, org.id, owner.branch_id, "m1@example.com")

        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org.id,
                department_id=source.id,
                transfer_to_department_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_transfer_target_belongs_to_different_org(
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

        source = await _make_department(db_session, org1.id, owner1.branch_id, name="Source", member_count=1)
        await _add_member(db_session, source.id, org1.id, owner1.branch_id, "m1@example.com")

        # Target belongs to org2 — cross-org transfer must be rejected
        cross_org_target = await _make_department(db_session, org2.id, owner2.branch_id, name="Other Target")

        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org1.id,
                department_id=source.id,
                transfer_to_department_id=cross_org_target.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_400_when_transfer_target_is_same_department(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id, member_count=1)
        await _add_member(db_session, dept.id, org.id, owner.branch_id, "m1@example.com")

        with pytest.raises(AppError) as exc_info:
            await delete_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                transfer_to_department_id=dept.id,  # same as source
            )

        assert exc_info.value.status_code == 400