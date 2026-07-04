"""
tests/services/departments/test_get_department.py

Unit tests for app/services/department/get_department.py

Coverage:
  get_departments()
    ✓ returns empty list when org has no departments
    ✓ returns all departments belonging to the org
    ✓ does not return departments from a different org
    ✓ departments are ordered by name
    ✓ pagination limit restricts returned count
    ✓ pagination offset skips correct departments
    ✓ total returned respects limit even when more exist

  get_department()
    ✓ returns the correct department by ID
    ✓ raises 404 when department not found
    ✓ raises 404 when department belongs to a different org
    ✓ returns correct fields on the department
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department
from app.services.department.get_department import get_department, get_departments
from tests.conftest import make_org_with_owner


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Engineering",
    description: str | None = None,
) -> Department:
    dept = Department(
        name=name,
        description=description,
        organization_id=organization_id,
        member_count=0,
    )
    db_session.add(dept)
    await db_session.flush()
    return dept


# ── get_departments ────────────────────────────────────────────────────────────

class TestGetDepartments:

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_departments(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_departments_in_org(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, name="Engineering")
        await _make_department(db_session, org.id, name="Marketing")
        await _make_department(db_session, org.id, name="Finance")

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
        )

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_does_not_return_departments_from_different_org(
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

        await _make_department(db_session, org1.id, name="Engineering")
        await _make_department(db_session, org2.id, name="Marketing")

        result = await get_departments(
            session=db_session,
            organization_id=org1.id,
        )

        assert len(result) == 1
        assert result[0].name == "Engineering"
        assert all(d.organization_id == org1.id for d in result)

    @pytest.mark.asyncio
    async def test_departments_ordered_by_name(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, name="Zebra Team")
        await _make_department(db_session, org.id, name="Alpha Team")
        await _make_department(db_session, org.id, name="Mango Team")

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
        )

        names = [d.name for d in result]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_limit_restricts_returned_count(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        for i in range(5):
            await _make_department(db_session, org.id, name=f"Dept {i:02d}")

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
            limit=2,
        )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_offset_skips_correct_departments(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, name="Aardvark")
        await _make_department(db_session, org.id, name="Baboon")
        await _make_department(db_session, org.id, name="Cheetah")

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
            offset=1,
            limit=10,
        )

        assert len(result) == 2
        assert result[0].name == "Baboon"
        assert result[1].name == "Cheetah"

    @pytest.mark.asyncio
    async def test_offset_beyond_total_returns_empty(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id, name="Engineering")
        await _make_department(db_session, org.id, name="Marketing")

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
            offset=100,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_department_objects(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_department(db_session, org.id)

        result = await get_departments(
            session=db_session,
            organization_id=org.id,
        )

        assert all(isinstance(d, Department) for d in result)


# ── get_department ─────────────────────────────────────────────────────────────

class TestGetDepartment:

    @pytest.mark.asyncio
    async def test_returns_correct_department_by_id(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept = await _make_department(db_session, org.id, name="Engineering")

        result = await get_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
        )

        assert result.id == dept.id
        assert result.name == "Engineering"

    @pytest.mark.asyncio
    async def test_returns_correct_fields(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        dept = await _make_department(
            db_session, org.id,
            name="Product",
            description="Builds the product",
        )

        result = await get_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept.id,
        )

        assert result.name == "Product"
        assert result.description == "Builds the product"
        assert result.organization_id == org.id

    @pytest.mark.asyncio
    async def test_returns_correct_department_when_multiple_exist(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept_a = await _make_department(db_session, org.id, name="Engineering")
        dept_b = await _make_department(db_session, org.id, name="Marketing")

        result = await get_department(
            session=db_session,
            organization_id=org.id,
            department_id=dept_b.id,
        )

        assert result.id == dept_b.id
        assert result.name == "Marketing"

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
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
            await get_department(
                session=db_session,
                organization_id=org2.id,
                department_id=dept.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_detail_is_informative(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_department(
                session=db_session,
                organization_id=org.id,
                department_id="00000000-0000-0000-0000-000000000000",
            )

        assert "not found" in exc_info.value.detail.lower()