"""
tests/services/department/test_update_department.py

Unit tests for app/services/department/update_department.py

Coverage:
  edit_department()
  assign_user_to_department()
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.department import Department, DepartmentMember
from app.services.department.update_department import (
    assign_user_to_department,
    edit_department,
)
from tests.conftest import (
    make_branch,
    make_org_with_owner,
    make_user_with_role,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,                    # required — branch_id is NOT NULL on Department
    name: str = "Engineering",
    description: str | None = None,
    avatar_initial: str | None = "E",
) -> Department:
    """Create a department scoped to a branch. branch_id is required
    because Department.branch_id is NOT NULL — there are no org-wide
    floating departments anymore."""
    dept = Department(
        name=name,
        description=description,
        avatar_initial=avatar_initial,
        organization_id=organization_id,
        branch_id=branch_id,
        member_count=0,
    )
    db_session.add(dept)
    await db_session.flush()
    return dept


async def _make_membership(
    db_session: AsyncSession,
    department_id: str,
    user_id: str,
) -> DepartmentMember:
    """Create a department membership."""
    membership = DepartmentMember(
        department_id=department_id,
        user_id=user_id,
    )
    db_session.add(membership)
    await db_session.flush()
    return membership


# ── edit_department ────────────────────────────────────────────────────────────

class TestEditDepartment:

    @pytest.mark.asyncio
    async def test_edits_name_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id, name="Engineering")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="Product Engineering",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Product Engineering"
        assert result.description == dept.description
        assert result.avatar_initial == dept.avatar_initial

    @pytest.mark.asyncio
    async def test_edits_description_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(
            db_session, org.id, owner.branch_id, name="Engineering", description="Old description"
        )

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name=None,
            description="New description",
            avatar_initial=None,
        )

        assert result.name == dept.name
        assert result.description == "New description"
        assert result.avatar_initial == dept.avatar_initial

    @pytest.mark.asyncio
    async def test_edits_avatar_initial_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(
            db_session, org.id, owner.branch_id, name="Engineering", avatar_initial="E"
        )

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name=None,
            description=None,
            avatar_initial="PE",
        )

        assert result.name == dept.name
        assert result.description == dept.description
        assert result.avatar_initial == "PE"

    @pytest.mark.asyncio
    async def test_edits_all_fields_at_once(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(
            db_session, org.id, owner.branch_id,
            name="Engineering", description="Old", avatar_initial="E",
        )

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="Product",
            description="New desc",
            avatar_initial="P",
        )

        assert result.name == "Product"
        assert result.description == "New desc"
        assert result.avatar_initial == "P"

    @pytest.mark.asyncio
    async def test_no_change_when_all_fields_none(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(
            db_session, org.id, owner.branch_id,
            name="Engineering", description="Desc", avatar_initial="E",
        )

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name=None,
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Engineering"
        assert result.description == "Desc"
        assert result.avatar_initial == "E"

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_name(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id, name="Engineering")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="  Product Engineering  ",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Product Engineering"

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
                name="New Name",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Department not found" in exc_info.value.detail

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
        dept = await _make_department(db_session, org1.id, owner1.branch_id, name="Engineering")

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,
                name="New Name",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_raises_409_when_name_conflicts_within_same_branch(
        self, db_session: AsyncSession
    ):
        """Name uniqueness is scoped per branch — two departments in the
        same branch cannot share a name."""
        org, owner = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, owner.branch_id, name="Engineering")
        product_dept = await _make_department(db_session, org.id, owner.branch_id, name="Product")

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org.id,
                department_id=product_dept.id,
                name="Engineering",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert "already exists" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_allows_same_name_case_insensitive_no_conflict(
        self, db_session: AsyncSession
    ):
        """Renaming a department to a different casing of its own name
        must not raise a conflict — it's the same row."""
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id, name="Engineering")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            name="engineering",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "engineering"

    @pytest.mark.asyncio
    async def test_raises_409_when_name_conflicts_case_insensitive(
        self, db_session: AsyncSession
    ):
        """Name conflict check is case-insensitive within the same branch."""
        org, owner = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, owner.branch_id, name="Engineering")
        product_dept = await _make_department(db_session, org.id, owner.branch_id, name="Product")

        with pytest.raises(HTTPException) as exc_info:
            await edit_department(
                session=db_session,
                organization_id=org.id,
                department_id=product_dept.id,
                name="ENGINEERING",
                description=None,
                avatar_initial=None,
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.asyncio
    async def test_no_conflict_when_same_name_in_different_branch(
        self, db_session: AsyncSession
    ):
        """Same department name is allowed across different branches of
        the same org — uniqueness is branch-scoped, not org-scoped."""
        org, owner = await make_org_with_owner(db_session)

        branch_b = make_branch(org.id, name="Downtown")
        db_session.add(branch_b)
        await db_session.flush()

        # "Engineering" already exists in branch_b
        await _make_department(db_session, org.id, branch_b.id, name="Engineering")
        # Renaming a dept in HQ to "Engineering" should be fine — different branch
        hq_dept = await _make_department(db_session, org.id, owner.branch_id, name="Product")

        result = await edit_department(
            session=db_session,
            organization_id=org.id,
            department_id=hq_dept.id,
            name="Engineering",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Engineering"

    @pytest.mark.asyncio
    async def test_no_conflict_when_same_name_in_different_org(
        self, db_session: AsyncSession
    ):
        """Same name in a completely different org is always allowed."""
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
        await _make_department(db_session, org1.id, owner1.branch_id, name="Engineering")
        product_dept = await _make_department(db_session, org2.id, owner2.branch_id, name="Product")

        result = await edit_department(
            session=db_session,
            organization_id=org2.id,
            department_id=product_dept.id,
            name="Engineering",
            description=None,
            avatar_initial=None,
        )

        assert result.name == "Engineering"


# ── assign_user_to_department ─────────────────────────────────────────────────

class TestAssignUserToDepartment:

    @pytest.mark.asyncio
    async def test_assigns_user_to_department_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="member@example.com",
            role_name="member",
            branch_id=owner.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        result = await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
            user_id=member.id,
        )

        assert result.department_id == dept.id
        assert result.user_id == member.id

    @pytest.mark.asyncio
    async def test_moves_user_from_old_department_to_new(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        old_dept = await _make_department(db_session, org.id, owner.branch_id, name="Old Dept")
        new_dept = await _make_department(db_session, org.id, owner.branch_id, name="New Dept")
        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="member@example.com",
            role_name="member",
            branch_id=owner.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        await _make_membership(db_session, old_dept.id, member.id)

        result = await assign_user_to_department(
            session=db_session,
            organization_id=org.id,
            department_id=new_dept.id,
            user_id=member.id,
        )

        assert result.department_id == new_dept.id
        assert result.user_id == member.id

        old_membership = await db_session.execute(
            select(DepartmentMember).where(
                DepartmentMember.user_id == member.id,
                DepartmentMember.department_id == old_dept.id,
            )
        )
        assert old_membership.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="member@example.com",
            role_name="member",
            branch_id=owner.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
                user_id=member.id,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Department not found" in exc_info.value.detail

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
        member = await make_user_with_role(
            db_session,
            organization_id=org2.id,
            email="member@example.com",
            role_name="member",
            branch_id=owner2.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,   # dept is in org1
                user_id=member.id,
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Department not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "User not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_404_when_user_belongs_to_different_org(
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
        member = await make_user_with_role(
            db_session,
            organization_id=org2.id,
            email="member@example.com",
            role_name="member",
            branch_id=owner2.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org1.id,   # asking as org1
                department_id=dept.id,
                user_id=member.id,          # user is in org2
            )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "User not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_409_when_user_already_in_target_department(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, owner.branch_id)
        member = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="member@example.com",
            role_name="member",
            branch_id=owner.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        await _make_membership(db_session, dept.id, member.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_department(
                session=db_session,
                organization_id=org.id,
                department_id=dept.id,
                user_id=member.id,
            )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert "already a member" in exc_info.value.detail