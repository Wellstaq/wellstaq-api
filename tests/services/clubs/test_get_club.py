"""
tests/services/clubs/test_get_club.py

Tests for app/services/club/get_club.py

Coverage:
  get_club:
    - Happy path fetch
    - 404 when club doesn't exist
    - 404 when club belongs to a different org (org isolation)

  get_clubs (org-wide):
    - Returns all clubs in the org, across branches
    - Excludes clubs from other orgs
    - privacy filter
    - offset/limit pagination
    - total reflects the filtered count, not just the page size
    - ordered by name

  get_branch_clubs:
    - Returns only clubs in the given branch
    - Excludes clubs from other branches in the same org
    - privacy filter
    - offset/limit pagination
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.club import Club
from app.services.club.get_club import get_branch_clubs, get_club, get_clubs
from app.services.errors import AppError

from tests.conftest import make_org_with_owner


async def _make_branch(db_session: AsyncSession, organization_id: str, **overrides) -> Branch:
    defaults = dict(name="Main Branch")
    branch = Branch(organization_id=organization_id, **{**defaults, **overrides})
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_club(
    db_session: AsyncSession,
    *,
    organization_id: str,
    branch_id: str,
    created_by: str,
    name: str = "Club",
    privacy: str = "public",
    **overrides,
) -> Club:
    """Bare Club factory — bypasses create_club() service since these
    tests only need rows to query, not the full creation workflow
    (branch-membership checks, name-uniqueness, auto-membership, etc.
    are covered in test_create_club.py)."""
    defaults = dict(
        description=None,
        leader_id=created_by,
        member_count=0,
    )
    club = Club(
        organization_id=organization_id,
        branch_id=branch_id,
        created_by=created_by,
        name=name,
        privacy=privacy,
        **{**defaults, **overrides},
    )
    db_session.add(club)
    await db_session.flush()
    return club


class TestGetClub:
    @pytest.mark.asyncio
    async def test_returns_club(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        club = await _make_club(
            db_session, organization_id=org.id, branch_id=branch.id, created_by=owner.id
        )

        result = await get_club(session=db_session, organization_id=org.id, club_id=club.id)

        assert result.id == club.id
        assert result.name == club.name

    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        with pytest.raises(AppError) as exc_info:
            await get_club(
                session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_club_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org_a, _owner_a = await make_org_with_owner(
            db_session, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, owner_b = await make_org_with_owner(
            db_session,
            org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )
        branch_b = await _make_branch(db_session, org_b.id)
        club_b = await _make_club(
            db_session, organization_id=org_b.id, branch_id=branch_b.id, created_by=owner_b.id
        )

        with pytest.raises(AppError) as exc_info:
            await get_club(session=db_session, organization_id=org_a.id, club_id=club_b.id)

        assert exc_info.value.status_code == 404


class TestGetClubs:
    @pytest.mark.asyncio
    async def test_returns_clubs_across_branches_in_org(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Branch A")
        branch_b = await _make_branch(db_session, org.id, name="Branch B")
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch_a.id,
            created_by=owner.id, name="Club A",
        )
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch_b.id,
            created_by=owner.id, name="Club B",
        )

        clubs, total = await get_clubs(session=db_session, organization_id=org.id)

        assert total == 2
        assert {c.name for c in clubs} == {"Club A", "Club B"}

    @pytest.mark.asyncio
    async def test_excludes_clubs_from_other_orgs(self, db_session: AsyncSession):
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
        await _make_club(
            db_session, organization_id=org_a.id, branch_id=branch_a.id,
            created_by=owner_a.id, name="Org A Club",
        )
        await _make_club(
            db_session, organization_id=org_b.id, branch_id=branch_b.id,
            created_by=owner_b.id, name="Org B Club",
        )

        clubs, total = await get_clubs(session=db_session, organization_id=org_a.id)

        assert total == 1
        assert clubs[0].name == "Org A Club"

    @pytest.mark.asyncio
    async def test_filters_by_privacy(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch.id,
            created_by=owner.id, name="Public Club", privacy="public",
        )
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch.id,
            created_by=owner.id, name="Private Club", privacy="private",
        )

        clubs, total = await get_clubs(
            session=db_session, organization_id=org.id, privacy="private"
        )

        assert total == 1
        assert clubs[0].name == "Private Club"

    @pytest.mark.asyncio
    async def test_pagination_offset_and_limit(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        for name in ["Alpha", "Bravo", "Charlie", "Delta"]:
            await _make_club(
                db_session, organization_id=org.id, branch_id=branch.id,
                created_by=owner.id, name=name,
            )

        page_1, total = await get_clubs(
            session=db_session, organization_id=org.id, offset=0, limit=2
        )
        page_2, _ = await get_clubs(
            session=db_session, organization_id=org.id, offset=2, limit=2
        )

        assert total == 4
        assert [c.name for c in page_1] == ["Alpha", "Bravo"]
        assert [c.name for c in page_2] == ["Charlie", "Delta"]

    @pytest.mark.asyncio
    async def test_total_reflects_filtered_count_not_page_size(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        for i in range(5):
            await _make_club(
                db_session, organization_id=org.id, branch_id=branch.id,
                created_by=owner.id, name=f"Club {i}",
            )

        clubs, total = await get_clubs(
            session=db_session, organization_id=org.id, offset=0, limit=2
        )

        assert len(clubs) == 2
        assert total == 5

    @pytest.mark.asyncio
    async def test_returns_empty_when_org_has_no_clubs(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        clubs, total = await get_clubs(session=db_session, organization_id=org.id)

        assert clubs == []
        assert total == 0


class TestGetBranchClubs:
    @pytest.mark.asyncio
    async def test_returns_only_clubs_in_given_branch(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Branch A")
        branch_b = await _make_branch(db_session, org.id, name="Branch B")
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch_a.id,
            created_by=owner.id, name="Club A",
        )
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch_b.id,
            created_by=owner.id, name="Club B",
        )

        clubs, total = await get_branch_clubs(
            session=db_session, organization_id=org.id, branch_id=branch_a.id
        )

        assert total == 1
        assert clubs[0].name == "Club A"

    @pytest.mark.asyncio
    async def test_excludes_clubs_from_other_branches(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch_a = await _make_branch(db_session, org.id, name="Branch A")
        branch_b = await _make_branch(db_session, org.id, name="Branch B")
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch_b.id,
            created_by=owner.id, name="Should Not Appear",
        )

        clubs, total = await get_branch_clubs(
            session=db_session, organization_id=org.id, branch_id=branch_a.id
        )

        assert clubs == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_filters_by_privacy_within_branch(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch.id,
            created_by=owner.id, name="Public Club", privacy="public",
        )
        await _make_club(
            db_session, organization_id=org.id, branch_id=branch.id,
            created_by=owner.id, name="Private Club", privacy="private",
        )

        clubs, total = await get_branch_clubs(
            session=db_session, organization_id=org.id, branch_id=branch.id,
            privacy="public",
        )

        assert total == 1
        assert clubs[0].name == "Public Club"

    @pytest.mark.asyncio
    async def test_pagination_offset_and_limit(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        for name in ["Alpha", "Bravo", "Charlie"]:
            await _make_club(
                db_session, organization_id=org.id, branch_id=branch.id,
                created_by=owner.id, name=name,
            )

        page_1, total = await get_branch_clubs(
            session=db_session, organization_id=org.id, branch_id=branch.id,
            offset=0, limit=2,
        )

        assert total == 3
        assert [c.name for c in page_1] == ["Alpha", "Bravo"]