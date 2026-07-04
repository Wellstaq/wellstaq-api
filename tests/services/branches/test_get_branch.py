"""
tests/services/branches/test_get_branch.py

Unit tests for app/services/branch/get_branch.py

Coverage:
  get_branch()
    - returns the correct branch by ID
    - returns correct fields on the branch
    - raises 404 when branch not found
    - raises 404 when branch belongs to a different org
    - returns correct branch when multiple exist in the same org

  get_branches()
    - returns empty list and zero total when org has no branches
    - returns all branches belonging to the org
    - total reflects full count regardless of limit
    - does not return branches from a different org
    - branches are ordered by name
    - limit restricts returned count
    - offset skips correct branches
    - offset beyond total returns empty list
    - returns Branch objects

  get_branch_members()
    - returns empty list and zero total for branch with no members
    - returns members assigned to the branch
    - returns correct total count
    - members are ordered by first_name then last_name
    - does not return members from a different branch in same org
    - does not return members from a different org
    - total reflects full count even when limit is applied
    - limit restricts returned members
    - offset skips correct members
    - raises 404 when branch not found
    - raises 404 when branch belongs to a different org
    - returns User objects not Branch objects
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User
from app.services.branch.get_branch import get_branch, get_branch_members, get_branches
from tests.conftest import make_org_with_owner, make_user_with_role


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


async def _make_user_in_branch(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
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
    )
    user.branch_id = branch_id
    db_session.add(user)
    await db_session.flush()
    return user


# ── get_branch ─────────────────────────────────────────────────────────────────

class TestGetBranch:

    @pytest.mark.asyncio
    async def test_returns_correct_branch_by_id(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Lagos")

        result = await get_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert result.id == branch.id
        assert result.name == "Lagos"

    @pytest.mark.asyncio
    async def test_returns_correct_fields(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Abuja")

        result = await get_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert result.organization_id == org.id
        assert result.name == "Abuja"

    @pytest.mark.asyncio
    async def test_returns_correct_branch_when_multiple_exist(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Lagos")
        branch_b = await _make_branch(db_session, org.id, name="Abuja")

        result = await get_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch_b.id,
        )

        assert result.id == branch_b.id
        assert result.name == "Abuja"

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_branch(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
            )

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
            await get_branch(
                session=db_session,
                organization_id=org2.id,
                branch_id=branch.id,
            )

        assert exc_info.value.status_code == 404


# ── get_branches ───────────────────────────────────────────────────────────────

class TestGetBranches:

    @pytest.mark.asyncio
    async def test_returns_empty_list_and_zero_total_when_no_branches(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)

        branches, total = await get_branches(
            session=db_session,
            organization_id=org.id,
        )

        assert branches == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_returns_all_branches_in_org(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id, name="Lagos")
        await _make_branch(db_session, org.id, name="Abuja")
        await _make_branch(db_session, org.id, name="Kano")

        branches, total = await get_branches(
            session=db_session,
            organization_id=org.id,
        )

        assert len(branches) == 3
        assert total == 3

    @pytest.mark.asyncio
    async def test_does_not_return_branches_from_different_org(
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
        await _make_branch(db_session, org1.id, name="Lagos")
        await _make_branch(db_session, org2.id, name="Nairobi")

        branches, total = await get_branches(
            session=db_session,
            organization_id=org1.id,
        )

        assert len(branches) == 1
        assert total == 1
        assert branches[0].name == "Lagos"

    @pytest.mark.asyncio
    async def test_branches_are_ordered_by_name(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id, name="Zebra Office")
        await _make_branch(db_session, org.id, name="Alpha Office")
        await _make_branch(db_session, org.id, name="Mango Office")

        branches, _ = await get_branches(
            session=db_session,
            organization_id=org.id,
        )

        names = [b.name for b in branches]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_total_reflects_full_count_regardless_of_limit(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        for i in range(5):
            await _make_branch(db_session, org.id, name=f"Branch {i:02d}")

        branches, total = await get_branches(
            session=db_session,
            organization_id=org.id,
            limit=2,
        )

        assert len(branches) == 2
        assert total == 5

    @pytest.mark.asyncio
    async def test_limit_restricts_returned_count(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        for i in range(5):
            await _make_branch(db_session, org.id, name=f"Branch {i:02d}")

        branches, _ = await get_branches(
            session=db_session,
            organization_id=org.id,
            limit=3,
        )

        assert len(branches) == 3

    @pytest.mark.asyncio
    async def test_offset_skips_correct_branches(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id, name="Aardvark Office")
        await _make_branch(db_session, org.id, name="Baboon Office")
        await _make_branch(db_session, org.id, name="Cheetah Office")

        branches, _ = await get_branches(
            session=db_session,
            organization_id=org.id,
            offset=1,
            limit=10,
        )

        assert len(branches) == 2
        assert branches[0].name == "Baboon Office"
        assert branches[1].name == "Cheetah Office"

    @pytest.mark.asyncio
    async def test_offset_beyond_total_returns_empty_list(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id, name="Lagos")
        await _make_branch(db_session, org.id, name="Abuja")

        branches, total = await get_branches(
            session=db_session,
            organization_id=org.id,
            offset=100,
        )

        assert branches == []
        assert total == 2

    @pytest.mark.asyncio
    async def test_returns_branch_objects(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id)

        branches, _ = await get_branches(
            session=db_session,
            organization_id=org.id,
        )

        assert all(isinstance(b, Branch) for b in branches)


# ── get_branch_members ─────────────────────────────────────────────────────────

class TestGetBranchMembers:

    @pytest.mark.asyncio
    async def test_returns_empty_list_and_zero_total_for_branch_with_no_members(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        members, total = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert members == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_returns_members_assigned_to_branch(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        user = await _make_user_in_branch(
            db_session, org.id, branch.id, "m1@example.com"
        )

        members, _ = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert len(members) == 1
        assert members[0].id == user.id

    @pytest.mark.asyncio
    async def test_returns_correct_total_count(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")
        await _make_user_in_branch(db_session, org.id, branch.id, "m2@example.com")
        await _make_user_in_branch(db_session, org.id, branch.id, "m3@example.com")

        _, total = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert total == 3

    @pytest.mark.asyncio
    async def test_members_ordered_by_first_name_then_last_name(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(
            db_session, org.id, branch.id, "zara@example.com",
            first_name="Zara", last_name="Ahmed",
        )
        await _make_user_in_branch(
            db_session, org.id, branch.id, "ada@example.com",
            first_name="Ada", last_name="Lovelace",
        )
        await _make_user_in_branch(
            db_session, org.id, branch.id, "bisi@example.com",
            first_name="Bisi", last_name="Okafor",
        )

        members, _ = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert [m.first_name for m in members] == ["Ada", "Bisi", "Zara"]

    @pytest.mark.asyncio
    async def test_orders_by_last_name_when_first_names_equal(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(
            db_session, org.id, branch.id, "chidi_z@example.com",
            first_name="Chidi", last_name="Zuma",
        )
        await _make_user_in_branch(
            db_session, org.id, branch.id, "chidi_a@example.com",
            first_name="Chidi", last_name="Ake",
        )

        members, _ = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert members[0].last_name == "Ake"
        assert members[1].last_name == "Zuma"

    @pytest.mark.asyncio
    async def test_does_not_return_members_from_different_branch(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Lagos")
        branch_b = await _make_branch(db_session, org.id, name="Abuja")
        await _make_user_in_branch(db_session, org.id, branch_a.id, "lagos@example.com")
        await _make_user_in_branch(db_session, org.id, branch_b.id, "abuja@example.com")

        members, total = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch_a.id,
        )

        assert len(members) == 1
        assert total == 1
        assert members[0].email == "lagos@example.com"

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
        branch1 = await _make_branch(db_session, org1.id, name="Lagos")
        branch2 = await _make_branch(db_session, org2.id, name="Nairobi")
        await _make_user_in_branch(db_session, org1.id, branch1.id, "org1@example.com")
        await _make_user_in_branch(db_session, org2.id, branch2.id, "org2@example.com")

        members, total = await get_branch_members(
            session=db_session,
            organization_id=org1.id,
            branch_id=branch1.id,
        )

        assert len(members) == 1
        assert total == 1
        assert members[0].email == "org1@example.com"

    @pytest.mark.asyncio
    async def test_total_reflects_full_count_even_when_limit_applied(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        for i in range(5):
            await _make_user_in_branch(
                db_session, org.id, branch.id, f"m{i}@example.com"
            )

        _, total = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            limit=2,
        )

        assert total == 5

    @pytest.mark.asyncio
    async def test_limit_restricts_returned_members(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        for i in range(5):
            await _make_user_in_branch(
                db_session, org.id, branch.id, f"m{i}@example.com"
            )

        members, _ = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            limit=2,
        )

        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_offset_skips_correct_members(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(
            db_session, org.id, branch.id, "aaa@example.com",
            first_name="Aaa", last_name="User",
        )
        await _make_user_in_branch(
            db_session, org.id, branch.id, "bbb@example.com",
            first_name="Bbb", last_name="User",
        )
        await _make_user_in_branch(
            db_session, org.id, branch.id, "ccc@example.com",
            first_name="Ccc", last_name="User",
        )

        members, _ = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            offset=1,
            limit=10,
        )

        assert len(members) == 2
        assert members[0].first_name == "Bbb"
        assert members[1].first_name == "Ccc"

    @pytest.mark.asyncio
    async def test_returns_user_objects(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_user_in_branch(db_session, org.id, branch.id, "m1@example.com")

        members, _ = await get_branch_members(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert all(isinstance(m, User) for m in members)

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await get_branch_members(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
            )

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
            await get_branch_members(
                session=db_session,
                organization_id=org2.id,
                branch_id=branch.id,
            )

        assert exc_info.value.status_code == 404