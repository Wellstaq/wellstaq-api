"""
tests/services/departments/test_department_member.py

Unit tests for app/services/department/department_member.py

Coverage:
  get_department_members()
   
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models import Department, User, Branch
from app.models.department import DepartmentMember
from app.services.department.department_member import get_department_members
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Engineering",
) -> tuple[Department, str]:  # Return dept and branch_id
    branch = Branch(name=f"Branch for {name}", organization_id=organization_id)
    db_session.add(branch)
    await db_session.flush()

    dept = Department(name=name, organization_id=organization_id, branch_id=branch.id, member_count=0)
    db_session.add(dept)
    await db_session.flush()
    return dept, branch.id  # Return both


async def _add_member(
    db_session: AsyncSession,
    department_id: str,
    organization_id: str,
    branch_id: str,  # Now required
    email: str,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
) -> User:
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        role_name="member",
        first_name=first_name,
        last_name=last_name,
        branch_id=branch_id,  # Proper branch_id
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(DepartmentMember(department_id=department_id, user_id=user.id))
    await db_session.flush()
    return user

    
async def _call(
    db_session: AsyncSession,
    organization_id: str,
    department_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[User], int]:
    return await get_department_members(
        session=db_session,
        organization_id=organization_id,
        department_id=department_id,
        offset=offset,
        limit=limit,
    )


# ── Empty department ───────────────────────────────────────────────────────────

class TestGetDepartmentMembersEmpty:

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_department_with_no_members(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, _ = await _make_department(db_session, org.id)  # Unpack both

        members, total = await _call(db_session, org.id, dept.id)

        assert members == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_returns_zero_total_for_empty_department(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, _ = await _make_department(db_session, org.id)

        _, total = await _call(db_session, org.id, dept.id)

        assert total == 0


# ── Basic member retrieval ─────────────────────────────────────────────────────

class TestGetDepartmentMembersBasic:

    @pytest.mark.asyncio
    async def test_returns_members_belonging_to_department(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)
        user = await _add_member(db_session, dept.id, org.id, branch_id, "m1@example.com")

        members, _ = await _call(db_session, org.id, dept.id)

        assert len(members) == 1
        assert members[0].id == user.id

    @pytest.mark.asyncio
    async def test_returns_correct_total_count(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)
        await _add_member(db_session, dept.id, org.id, branch_id, "m1@example.com")
        await _add_member(db_session, dept.id, org.id, branch_id, "m2@example.com")
        await _add_member(db_session, dept.id, org.id, branch_id, "m3@example.com")

        _, total = await _call(db_session, org.id, dept.id)

        assert total == 3

    @pytest.mark.asyncio
    async def test_returns_user_objects_not_membership_objects(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)
        await _add_member(db_session, dept.id, org.id, branch_id, "m1@example.com")

        members, _ = await _call(db_session, org.id, dept.id)

        assert all(isinstance(m, User) for m in members)

    @pytest.mark.asyncio
    async def test_members_ordered_by_first_name_then_last_name(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)

        await _add_member(
            db_session, dept.id, org.id, branch_id, "zara@example.com",
            first_name="Zara", last_name="Ahmed",
        )
        await _add_member(
            db_session, dept.id, org.id, branch_id, "ada@example.com",
            first_name="Ada", last_name="Lovelace",
        )
        await _add_member(
            db_session, dept.id, org.id, branch_id, "bisi@example.com",
            first_name="Bisi", last_name="Okafor",
        )

        members, _ = await _call(db_session, org.id, dept.id)

        assert [m.first_name for m in members] == ["Ada", "Bisi", "Zara"]

    @pytest.mark.asyncio
    async def test_orders_by_last_name_when_first_names_are_equal(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)

        await _add_member(
            db_session, dept.id, org.id, branch_id, "chidi_z@example.com",
            first_name="Chidi", last_name="Zuma",
        )
        await _add_member(
            db_session, dept.id, org.id, branch_id, "chidi_a@example.com",
            first_name="Chidi", last_name="Ake",
        )

        members, _ = await _call(db_session, org.id, dept.id)

        assert members[0].last_name == "Ake"
        assert members[1].last_name == "Zuma"


# ── Isolation ──────────────────────────────────────────────────────────────────

class TestGetDepartmentMembersIsolation:

    @pytest.mark.asyncio
    async def test_does_not_return_members_from_different_department(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept_a, branch_id_a = await _make_department(db_session, org.id, name="Engineering")
        dept_b, branch_id_b = await _make_department(db_session, org.id, name="Marketing")

        await _add_member(db_session, dept_a.id, org.id, branch_id_a, "eng@example.com")
        await _add_member(db_session, dept_b.id, org.id, branch_id_b, "mkt@example.com")

        members, total = await _call(db_session, org.id, dept_a.id)

        assert len(members) == 1
        assert total == 1
        assert members[0].email == "eng@example.com"

    @pytest.mark.asyncio
    async def test_does_not_return_members_from_different_org(
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

        dept1, branch_id1 = await _make_department(db_session, org1.id)
        dept2, branch_id2 = await _make_department(db_session, org2.id)

        await _add_member(db_session, dept1.id, org1.id, branch_id1, "org1member@example.com")
        await _add_member(db_session, dept2.id, org2.id, branch_id2, "org2member@example.com")

        members, total = await _call(db_session, org1.id, dept1.id)

        assert len(members) == 1
        assert total == 1
        assert members[0].email == "org1member@example.com"


# ── Pagination ─────────────────────────────────────────────────────────────────

class TestGetDepartmentMembersPagination:

    @pytest.mark.asyncio
    async def test_limit_restricts_returned_members(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)

        for i in range(5):
            await _add_member(db_session, dept.id, org.id, branch_id, f"m{i}@example.com")

        members, _ = await _call(db_session, org.id, dept.id, limit=2)

        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_total_reflects_full_count_even_when_limit_applied(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)

        for i in range(5):
            await _add_member(db_session, dept.id, org.id, branch_id, f"m{i}@example.com")

        _, total = await _call(db_session, org.id, dept.id, limit=2)

        assert total == 5

    @pytest.mark.asyncio
    async def test_offset_skips_correct_number_of_members(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)

        # Add in alphabetical order so ordering is predictable
        await _add_member(
            db_session, dept.id, org.id, branch_id, "aaa@example.com",
            first_name="Aaa", last_name="User",
        )
        await _add_member(
            db_session, dept.id, org.id, branch_id, "bbb@example.com",
            first_name="Bbb", last_name="User",
        )
        await _add_member(
            db_session, dept.id, org.id, branch_id, "ccc@example.com",
            first_name="Ccc", last_name="User",
        )

        members, _ = await _call(db_session, org.id, dept.id, offset=1, limit=10)

        assert len(members) == 2
        assert members[0].first_name == "Bbb"
        assert members[1].first_name == "Ccc"

    @pytest.mark.asyncio
    async def test_offset_beyond_total_returns_empty(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        dept, branch_id = await _make_department(db_session, org.id)

        await _add_member(db_session, dept.id, org.id, branch_id, "m1@example.com")
        await _add_member(db_session, dept.id, org.id, branch_id, "m2@example.com")

        members, total = await _call(db_session, org.id, dept.id, offset=100)

        assert members == []
        assert total == 2  # total is still full count


# ── Not found errors ───────────────────────────────────────────────────────────

class TestGetDepartmentMembersNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await _call(
                db_session, org.id,
                "00000000-0000-0000-0000-000000000000",
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
        dept, _ = await _make_department(db_session, org1.id)

        with pytest.raises(HTTPException) as exc_info:
            await _call(db_session, org2.id, dept.id)

        assert exc_info.value.status_code == 404