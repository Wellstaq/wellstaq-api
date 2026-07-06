"""
tests/services/clubs/test_create_club.py

Tests for app/services/club/create_club.py

Coverage:
  - Happy path: club created, creator becomes leader + first member
  - Branch must belong to the same organization
  - Creator must belong to the target branch
  - Club name uniqueness is enforced org-wide
  - member_count and ClubMember row are correct after creation
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, ClubMember, Branch
from app.services.club.create_club import create_club
from app.services.errors import AppError

from tests.conftest import make_org_with_owner, make_user_with_role


async def _make_branch(db_session: AsyncSession, organization_id: str, **overrides) -> Branch:
    """Minimal Branch factory. Adjust fields here if Branch has more
    required columns than organization_id/name."""
    defaults = dict(name="Main Branch")
    branch = Branch(organization_id=organization_id, **{**defaults, **overrides})
    db_session.add(branch)
    await db_session.flush()
    return branch


class TestCreateClubHappyPath:
    @pytest.mark.asyncio
    async def test_creates_club_with_correct_fields(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club = await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Running Club",
            description="Weekly 5k on Saturdays",
            privacy="public",
            created_by=owner.id,
        )

        assert club.id is not None
        assert club.name == "Running Club"
        assert club.description == "Weekly 5k on Saturdays"
        assert club.privacy == "public"
        assert club.organization_id == org.id
        assert club.branch_id == branch.id
        assert club.created_by == owner.id

    @pytest.mark.asyncio
    async def test_creator_becomes_leader(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club = await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Book Club",
            description=None,
            privacy="private",
            created_by=owner.id,
        )

        assert club.leader_id == owner.id

    @pytest.mark.asyncio
    async def test_creator_added_as_first_member(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club = await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Chess Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        result = await db_session.execute(
            select(ClubMember).where(ClubMember.club_id == club.id)
        )
        members = result.scalars().all()
        assert len(members) == 1
        assert members[0].user_id == owner.id

    @pytest.mark.asyncio
    async def test_member_count_is_one_after_creation(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club = await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Hiking Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        assert club.member_count == 1

    @pytest.mark.asyncio
    async def test_name_is_stripped(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        club = await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="   Padel Club   ",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        assert club.name == "Padel Club"


class TestCreateClubBranchValidation:
    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(AppError) as exc_info:
            await create_club(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
                name="Ghost Club",
                description=None,
                privacy="public",
                created_by=owner.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org_a, owner_a = await make_org_with_owner(
            db_session, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )
        branch_b = await _make_branch(db_session, org_b.id)

        with pytest.raises(AppError) as exc_info:
            await create_club(
                session=db_session,
                organization_id=org_a.id,
                branch_id=branch_b.id,
                name="Cross Org Club",
                description=None,
                privacy="public",
                created_by=owner_a.id,
            )

        assert exc_info.value.status_code == 404


class TestCreateClubMembershipValidation:
    @pytest.mark.asyncio
    async def test_raises_403_when_creator_not_in_branch(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        # Deliberately do NOT assign owner.branch_id -> owner belongs to
        # no branch, or a different one.

        with pytest.raises(AppError) as exc_info:
            await create_club(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="Unauthorized Club",
                description=None,
                privacy="public",
                created_by=owner.id,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_403_when_creator_belongs_to_different_branch(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Branch A")
        branch_b = await _make_branch(db_session, org.id, name="Branch B")
        owner.branch_id = branch_a.id
        await db_session.flush()

        with pytest.raises(AppError) as exc_info:
            await create_club(
                session=db_session,
                organization_id=org.id,
                branch_id=branch_b.id,
                name="Wrong Branch Club",
                description=None,
                privacy="public",
                created_by=owner.id,
            )

        assert exc_info.value.status_code == 403


class TestCreateClubNameUniqueness:
    @pytest.mark.asyncio
    async def test_raises_409_on_duplicate_name_same_org(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Yoga Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        with pytest.raises(AppError) as exc_info:
            await create_club(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="Yoga Club",
                description=None,
                privacy="public",
                created_by=owner.id,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_duplicate_check_is_case_and_whitespace_sensitive_to_stripped_name(
        self, db_session: AsyncSession
    ):
        """
        create_club() compares Club.name == name.strip() with no
        case-folding, so "Yoga Club" and "  Yoga Club  " collide
        (both strip to the same value) but "yoga club" (different
        case) does not, given the current implementation.
        """
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        await create_club(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Yoga Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        # Same name with surrounding whitespace -> collides after strip()
        with pytest.raises(AppError) as exc_info:
            await create_club(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="   Yoga Club   ",
                description=None,
                privacy="public",
                created_by=owner.id,
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_same_name_allowed_in_different_orgs(self, db_session: AsyncSession):
        org_a, owner_a = await make_org_with_owner(
            db_session, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, owner_b = await make_org_with_owner(
            db_session,
            org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )
        branch_a = await _make_branch(db_session, org_a.id)
        branch_b = await _make_branch(db_session, org_b.id)
        owner_a.branch_id = branch_a.id
        owner_b.branch_id = branch_b.id
        await db_session.flush()

        club_a = await create_club(
            session=db_session,
            organization_id=org_a.id,
            branch_id=branch_a.id,
            name="Shared Name Club",
            description=None,
            privacy="public",
            created_by=owner_a.id,
        )
        club_b = await create_club(
            session=db_session,
            organization_id=org_b.id,
            branch_id=branch_b.id,
            name="Shared Name Club",
            description=None,
            privacy="public",
            created_by=owner_b.id,
        )

        assert club_a.id != club_b.id
        assert club_a.organization_id != club_b.organization_id