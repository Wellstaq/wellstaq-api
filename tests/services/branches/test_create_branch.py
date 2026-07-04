"""
tests/services/branches/test_create_branch.py

Unit tests for app/services/branches/create_branch.py

Coverage:
  create_branch()
     creates a branch successfully
     returns branch with correct fields
     branch id is populated after creation
     timestamps are set after creation
     manager_id is None by default
     created_by is stored correctly
     name is stripped of whitespace before storage
     raises 404 when organization not found
     raises 409 when branch name already exists in org (exact match)
     raises 409 when branch name already exists in org (case-insensitive)
     allows same name in different organizations
     allows different names in same organization
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch
from app.services.branch.create_branch import create_branch
from tests.conftest import make_org_with_owner


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _create(
    db_session: AsyncSession,
    organization_id: str,
    created_by: str,
    name: str = "Lagos Branch",
) -> Branch:
    return await create_branch(
        session=db_session,
        organization_id=organization_id,
        name=name,
        created_by=created_by,
    )


# ── Happy path ─────────────────────────────────────────────────────────────────

class TestCreateBranchSuccess:

    @pytest.mark.asyncio
    async def test_creates_branch_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id)

        assert branch is not None
        assert branch.organization_id == org.id

    @pytest.mark.asyncio
    async def test_returns_branch_with_correct_fields(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id, name="Abuja Branch")

        assert branch.name == "Abuja Branch"
        assert branch.organization_id == org.id
        assert branch.created_by == owner.id

    @pytest.mark.asyncio
    async def test_branch_id_is_populated_after_creation(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id)

        assert branch.id is not None
        parts = branch.id.split("-")
        assert len(parts) == 5

    @pytest.mark.asyncio
    async def test_timestamps_are_set_after_creation(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id)

        assert branch.created_at is not None
        assert branch.updated_at is not None

    @pytest.mark.asyncio
    async def test_manager_id_is_none_by_default(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id)

        assert branch.manager_id is None

    @pytest.mark.asyncio
    async def test_created_by_is_stored_correctly(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id)

        assert branch.created_by == owner.id

    @pytest.mark.asyncio
    async def test_name_is_stripped_of_whitespace(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch = await _create(db_session, org.id, owner.id, name="  Lagos Branch  ")

        assert branch.name == "Lagos Branch"

    @pytest.mark.asyncio
    async def test_allows_same_name_in_different_organizations(
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

        branch1 = await _create(db_session, org1.id, owner1.id, name="Lagos")
        branch2 = await _create(db_session, org2.id, owner2.id, name="Lagos")

        assert branch1.organization_id == org1.id
        assert branch2.organization_id == org2.id

    @pytest.mark.asyncio
    async def test_allows_different_names_in_same_org(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        branch1 = await _create(db_session, org.id, owner.id, name="Lagos")
        branch2 = await _create(db_session, org.id, owner.id, name="Abuja")

        assert branch1.id != branch2.id
        assert branch1.name == "Lagos"
        assert branch2.name == "Abuja"


# ── Not found errors ───────────────────────────────────────────────────────────

class TestCreateBranchNotFound:

    @pytest.mark.asyncio
    async def test_raises_404_when_organization_not_found(
        self, db_session: AsyncSession
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_branch(
                session=db_session,
                organization_id="00000000-0000-0000-0000-000000000000",
                name="Lagos",
                created_by="some-user-id",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_detail_is_informative(self, db_session: AsyncSession):
        with pytest.raises(HTTPException) as exc_info:
            await create_branch(
                session=db_session,
                organization_id="00000000-0000-0000-0000-000000000000",
                name="Lagos",
                created_by="some-user-id",
            )

        assert "not found" in exc_info.value.detail.lower()


# ── Name uniqueness ────────────────────────────────────────────────────────────

class TestCreateBranchNameUniqueness:

    @pytest.mark.asyncio
    async def test_raises_409_when_name_already_exists_exact_match(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        await _create(db_session, org.id, owner.id, name="Lagos")

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="Lagos")

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_name_conflicts_case_insensitive(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        await _create(db_session, org.id, owner.id, name="Lagos")

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="LAGOS")

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_for_mixed_case_conflict(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        await _create(db_session, org.id, owner.id, name="Head Office")

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="head office")

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_409_detail_mentions_the_conflicting_name(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        await _create(db_session, org.id, owner.id, name="Lagos")

        with pytest.raises(HTTPException) as exc_info:
            await _create(db_session, org.id, owner.id, name="Lagos")

        assert "Lagos" in exc_info.value.detail