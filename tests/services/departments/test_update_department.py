"""
tests/services/departments/test_update_department.py

Unit tests for app/services/department/update_department.py

Coverage:
  edit_department()
    ✓ updates name successfully
    ✓ updates description successfully
    ✓ updates avatar_initial successfully
    ✓ updates all fields at once
    ✓ name is stripped of whitespace before storage
    ✓ only provided fields are updated — unprovided fields unchanged
    ✓ allows updating name to the same value (case-insensitive no-op)
    ✓ allows updating name to different casing of same name
    ✓ raises 404 when department not found
    ✓ raises 404 when department belongs to different org
    ✓ raises 409 when new name conflicts with existing department (case-insensitive)
    ✓ does not raise 409 when name is unchanged
    ✓ returns updated department

  assign_user_to_department()
    ✓ assigns a user to a department with no prior membership
    ✓ returns the new DepartmentMember row
    ✓ new membership row has correct department_id and user_id
    ✓ moves user from old department to new one atomically
    ✓ old membership row is deleted after reassignment
    ✓ raises 404 when target department not found
    ✓ raises 404 when target department belongs to different org
    ✓ raises 404 when user not found
    ✓ raises 404 when user belongs to different org
    ✓ raises 409 when user is already in the target department
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, User
from app.models.department import DepartmentMember
from app.services.department.update_department import (
    assign_user_to_department,
    edit_department,
)
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Engineering",
    description: str | None = None,
    avatar_initial: str | None = None,
) -> Department:
    dept = Department(
        name=name,
        description=description,
        avatar_initial=avatar_initial,
        organization_id=organization_id,
        member_count=0,
    )
    db_session.add(dept)
    await db_session.flush()
    return dept


async def _make_user(
    db_session: AsyncSession,
    organization_id: str,
    email: str,
) -> User:
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        role_name="member",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_membership(
    db_session: AsyncSession,
    department_id: str,
    user_id: str,
) -> DepartmentMember:
    membership = DepartmentMember(department_id=department_id, user_id=user_id)
    db_session.add(membership)
    await db_session.flush()
    return membership


# ── edit_department ────────────────────────────────────────────────────────────

class TestEditDepartment:

    @pytest.mark.asyncio
    async def test_updates_name_successfully(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, name="Old Name")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="New Name",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "New Name"

    @pytest.mark.asyncio
    async def test_updates_description_successfully(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name=None,
            description="Builds the product",
            avatar_initial=None,
        )

        assert result.description == "Builds the product"

    @pytest.mark.asyncio
    async def test_updates_avatar_initial_successfully(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name=None,
            description=None,
            avatar_initial="EN",
        )

        assert result.avatar_initial == "EN"

    @pytest.mark.asyncio
    async def test_updates_all_fields_at_once(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, name="Old")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="New",
            description="Updated desc",
            avatar_initial="NW",
        )

        assert result.name == "New"
        assert result.description == "Updated desc"
        assert result.avatar_initial == "NW"

    @pytest.mark.asyncio
    async def test_name_stripped_of_whitespace(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="  Product  ",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Product"

    @pytest.mark.asyncio
    async def test_unprovided_fields_are_not_changed(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(
            db_session, org.id,
            name="Engineering",
            description="Original desc",
            avatar_initial="EN",
        )

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="Updated Name",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Updated Name"
        assert result.description == "Original desc"
        assert result.avatar_initial == "EN"

    @pytest.mark.asyncio
    async def test_does_not_raise_when_name_is_unchanged(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, name="Engineering")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="Engineering",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Engineering"

    @pytest.mark.asyncio
    async def test_does_not_raise_for_different_casing_of_same_name(
        self, db_session: AsyncSession
    ):
        """Changing 'engineering' to 'Engineering' is a casing update on
        the same department — should not trigger the uniqueness check."""
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, name="engineering")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="Engineering",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Engineering"

    @pytest.mark.asyncio
    async def test_returns_updated_department(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="Updated",
            description=None,
            avatar_initial=None,
        )

        assert isinstance(result, Department)
        assert result.id == dept.id

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
                name="New Name",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_department_belongs_to_different_org(
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
        dept = await _make_department(db_session, org1.id)

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,
                name="New Name",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_name_conflicts_with_existing(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, name="Marketing")
        dept = await _make_department(db_session, org.id, name="Engineering")

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                name="Marketing",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_for_case_insensitive_name_conflict(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, name="Marketing")
        dept = await _make_department(db_session, org.id, name="Engineering")

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                name="MARKETING",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == 409


# ── assign_user_to_department ──────────────────────────────────────────────────

class TestAssignUserToDepartment:

    @pytest.mark.asyncio
    async def test_assigns_user_with_no_prior_membership(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)
        user = await _make_user(db_session, org.id, "user@example.com")

        result = await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        assert result.department_id == dept.id
        assert result.user_id == user.id

    @pytest.mark.asyncio
    async def test_returns_department_member_row(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)
        user = await _make_user(db_session, org.id, "user@example.com")

        result = await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        assert isinstance(result, DepartmentMember)
        assert result.id is not None

    @pytest.mark.asyncio
    async def test_membership_row_has_correct_fields(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)
        user = await _make_user(db_session, org.id, "user@example.com")

        result = await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=user.id,
        )

        db_result = await db_session.execute(
            select(DepartmentMember).where(DepartmentMember.id == result.id)
        )
        fetched = db_result.scalar_one()
        assert fetched.department_id == dept.id
        assert fetched.user_id == user.id

    @pytest.mark.asyncio
    async def test_moves_user_from_old_department_atomically(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        old_dept = await _make_department(db_session, org.id, name="Old")
        new_dept = await _make_department(db_session, org.id, name="New")
        user = await _make_user(db_session, org.id, "user@example.com")
        await _add_membership(db_session, old_dept.id, user.id)

        result = await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=new_dept.id,
            user_id=user.id,
        )

        assert result.department_id == new_dept.id

    @pytest.mark.asyncio
    async def test_old_membership_row_is_deleted_after_reassignment(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        old_dept = await _make_department(db_session, org.id, name="Old")
        new_dept = await _make_department(db_session, org.id, name="New")
        user = await _make_user(db_session, org.id, "user@example.com")
        old_membership = await _add_membership(db_session, old_dept.id, user.id)
        old_membership_id = old_membership.id

        await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=new_dept.id,
            user_id=user.id,
        )

        result = await db_session.execute(
            select(DepartmentMember).where(DepartmentMember.id == old_membership_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_only_one_membership_row_exists_after_reassignment(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        old_dept = await _make_department(db_session, org.id, name="Old")
        new_dept = await _make_department(db_session, org.id, name="New")
        user = await _make_user(db_session, org.id, "user@example.com")
        await _add_membership(db_session, old_dept.id, user.id)

        await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=new_dept.id,
            user_id=user.id,
        )

        result = await db_session.execute(
            select(DepartmentMember).where(DepartmentMember.user_id == user.id)
        )
        memberships = result.scalars().all()
        assert len(memberships) == 1
        assert memberships[0].department_id == new_dept.id

    @pytest.mark.asyncio
    async def test_raises_404_when_target_department_not_found(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        user = await _make_user(db_session, org.id, "user@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
                user_id=user.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_department_belongs_to_different_org(
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
        dept_org2 = await _make_department(db_session, org2.id)
        user = await _make_user(db_session, org1.id, "user@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org1.id,
                department_id=dept_org2.id,
                user_id=user.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_belongs_to_different_org(
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
        dept = await _make_department(db_session, org1.id)
        user_org2 = await _make_user(db_session, org2.id, "user@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org1.id,
                department_id=dept.id,
                user_id=user_org2.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_user_already_in_target_department(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id)
        user = await _make_user(db_session, org.id, "user@example.com")
        await _add_membership(db_session, dept.id, user.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id=user.id,
            )

        assert exc_info.value.status_code == 409