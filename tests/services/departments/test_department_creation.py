"""
tests/services/departments/test_department_creation.py

Unit tests for app/services/department/department_creation.py

Coverage:
  create_department()
    ✓ creates a department successfully at org level
    ✓ creates a department successfully scoped to a branch
    ✓ returns the created department with correct fields
    ✓ department id is populated after creation
    ✓ department timestamps are set after creation
    ✓ member_count defaults to 0
    ✓ optional fields (description, avatar_initial) are stored correctly
    ✓ optional fields default to None when not provided
    ✓ name is stripped of leading/trailing whitespace before storage
    ✓ raises 404 when organization not found
    ✓ raises 403 when organization is deactivated
    ✓ raises 404 when branch_id provided but branch not found
    ✓ raises 404 when branch_id belongs to a different org
    ✓ raises 409 when org-level dept name already exists org-wide (case-insensitive)
    ✓ raises 409 when branch-level dept name already exists in same branch
    ✓ allows same name in different branches without conflict
    ✓ allows same name in different orgs without conflict
    ✓ allows same name org-wide and branch-scoped simultaneously
    ✓ name uniqueness check is case-insensitive
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models import Department, Organization, Branch
from app.services.department.department_creation import create_department
from tests.conftest import make_org_with_owner


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Lagos Branch",
) -> Branch:
    branch = Branch(organization_id=organization_id, name=name)
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _create(
    db_session: AsyncSession,
    organization_id: str,
    created_by: str,
    *,
    name: str = "Engineering",
    description: str | None = None,
    avatar_initial: str | None = None,
    branch_id: str | None = None,
) -> Department:
    return await create_department(
        session=db_session,
        organization_id=organization_id,
        name=name,
        description=description,
        avatar_initial=avatar_initial,
        created_by=created_by,
        branch_id=branch_id,
    )


# ── Happy path: org-level ──────────────────────────────────────────────────────

class TestCreateDepartmentOrgLevel:

    @pytest.mark.asyncio
    async def test_creates_department_at_org_level(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(db_session, org.id, owner.id)

        result = await db_session.execute(
            select(Department).where(Department.id == dept.id)
        )
        fetched = result.scalar_one()
        assert fetched.organization_id == org.id
        assert fetched.name == "Engineering"
        assert fetched.branch_id is None

    @pytest.mark.asyncio
    async def test_returns_department_with_correct_fields(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(
            db_session, org.id, owner.id,
            name="Product",
            description="Builds the product",
            avatar_initial="PR",
        )

        assert dept.name == "Product"
        assert dept.description == "Builds the product"
        assert dept.avatar_initial == "PR"
        assert dept.organization_id == org.id
        assert dept.created_by == owner.id
        assert dept.branch_id is None

    @pytest.mark.asyncio
    async def test_department_id_is_populated(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(db_session, org.id, owner.id)

        assert dept.id is not None
        parts = dept.id.split("-")
        assert len(parts) == 5

    @pytest.mark.asyncio
    async def test_timestamps_are_set(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(db_session, org.id, owner.id)

        assert dept.created_at is not None
        assert dept.updated_at is not None

    @pytest.mark.asyncio
    async def test_member_count_defaults_to_zero(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(db_session, org.id, owner.id)

        assert dept.member_count == 0

    @pytest.mark.asyncio
    async def test_optional_fields_default_to_none(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(db_session, org.id, owner.id)

        assert dept.description is None
        assert dept.avatar_initial is None

    @pytest.mark.asyncio
    async def test_name_is_stripped_of_whitespace(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        dept = await _create(db_session, org.id, owner.id, name="  Engineering  ")

        assert dept.name == "Engineering"


# ── Happy path: branch-level ───────────────────────────────────────────────────

class TestCreateDepartmentBranchLevel:

    @pytest.mark.asyncio
    async def test_creates_department_scoped_to_branch(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        dept = await _create(db_session, org.id, owner.id, branch_id=branch.id)

        assert dept.branch_id == branch.id
        assert dept.organization_id == org.id

    @pytest.mark.asyncio
    async def test_allows_same_name_in_different_branches(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Lagos")
        branch_b = await _make_branch(db_session, org.id, name="Abuja")

        dept_a = await _create(
            db_session, org.id, owner.id,
            name="Engineering", branch_id=branch_a.id,
        )
        dept_b = await _create(
            db_session, org.id, owner.id,
            name="Engineering", branch_id=branch_b.id,
        )

        assert dept_a.id != dept_b.id
        assert dept_a.branch_id == branch_a.id
        assert dept_b.branch_id == branch_b.id

    @pytest.mark.asyncio
    async def test_allows_same_name_org_wide_and_branch_scoped(
        self, db_session: AsyncSession
    ):
        """Org-wide 'Engineering' and branch-scoped 'Engineering' can coexist
        — they occupy different scope buckets."""
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        org_dept = await _create(db_session, org.id, owner.id, name="Engineering")
        branch_dept = await _create(
            db_session, org.id, owner.id,
            name="Engineering", branch_id=branch.id,
        )

        assert org_dept.branch_id is None
        assert branch_dept.branch_id == branch.id

    @pytest.mark.asyncio
    async def test_allows_same_name_in_different_orgs(self, db_session: AsyncSession):
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

        dept1 = await _create(db_session, org1.id, owner1.id, name="Engineering")
        dept2 = await _create(db_session, org2.id, owner2.id, name="Engineering")

        assert dept1.organization_id == org1.id
        assert dept2.organization_id == org2.id


# ── Not found / deactivated errors ────────────────────────────────────────────

class TestCreateDepartmentNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_organization_not_found(self, db_session: AsyncSession):
        with pytest.raises(HTTPException) as exc_info:
            await create_department(
                session=db_session,
                organization_id="00000000-0000-0000-0000-000000000000",
                name="Engineering",
                description=None,
                avatar_initial=None,
                created_by="some-user-id",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_when_organization_is_deactivated(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        org.is_active = False
        await db_session.flush()

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await _create(
                db_session, org.id, owner.id,
                branch_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
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
        branch_org2 = await _make_branch(db_session, org2.id)

        with pytest.raises(HTTPException) as exc_info:
            await _create(
                db_session, org1.id, owner1.id,
                branch_id=branch_org2.id,
            )

        assert exc_info.value.status_code == 404


# ── Name uniqueness ────────────────────────────────────────────────────────────

class TestCreateDepartmentNameUniqueness:

    @pytest.mark.asyncio
    async def test_raises_409_when_org_level_name_already_exists(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        await _create(db_session, org.id, owner.id, name="Engineering")

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="Engineering")

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_level_name_already_exists_in_same_branch(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        await _create(db_session, org.id, owner.id, name="Engineering", branch_id=branch.id)

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="Engineering", branch_id=branch.id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_name_uniqueness_is_case_insensitive(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        await _create(db_session, org.id, owner.id, name="Engineering")

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="ENGINEERING")

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_name_uniqueness_case_insensitive_in_branch(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        await _create(db_session, org.id, owner.id, name="hr", branch_id=branch.id)

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="HR", branch_id=branch.id)

        assert exc_info.value.status_code == 409