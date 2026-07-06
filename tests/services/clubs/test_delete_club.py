"""
tests/services/clubs/test_delete_club.py

Tests for app/services/club/delete_club.py

Coverage:
  - Happy path: club deleted, return value shape
  - ClubMember rows are cascade-deleted with the club
  - Club member Users themselves are NOT deleted
  - 404 when club doesn't exist
  - 404 when club exists but belongs to a different org (org isolation)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.club import Club, ClubMember
from app.models import User
from app.services.club.create_club import create_club
from app.services.club.delete_club import delete_club
from app.services.errors import AppError

from tests.conftest import make_org_with_owner


async def _make_branch(db_session: AsyncSession, organization_id: str, **overrides) -> Branch:
    """Minimal Branch factory. Adjust fields here if Branch has more
    required columns than organization_id/name."""
    defaults = dict(name="Main Branch")
    branch = Branch(organization_id=organization_id, **{**defaults, **overrides})
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_club_with_owner(db_session: AsyncSession, *, org_kwargs=None, user_kwargs=None, **club_overrides):
    """Sets up an org + branch-assigned owner, then creates a club via
    the real create_club() service so the club is in a realistic state
    (leader assigned, creator auto-added as member)."""
    org, owner = await make_org_with_owner(
        db_session, org_kwargs=org_kwargs, user_kwargs=user_kwargs
    )
    branch = await _make_branch(db_session, org.id)
    owner.branch_id = branch.id
    await db_session.flush()

    defaults = dict(
        organization_id=org.id,
        branch_id=branch.id,
        name="Running Club",
        description=None,
        privacy="public",
        created_by=owner.id,
    )
    club = await create_club(session=db_session, **{**defaults, **club_overrides})
    return org, owner, branch, club


class TestDeleteClubHappyPath:
    @pytest.mark.asyncio
    async def test_deletes_club(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        await delete_club(session=db_session, organization_id=org.id, club_id=club.id)

        result = await db_session.execute(select(Club).where(Club.id == club.id))
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_returns_club_id_and_organization_id(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        response = await delete_club(
            session=db_session, organization_id=org.id, club_id=club.id
        )

        assert response == {
            "club_id": club.id,
            "organization_id": org.id,
        }

    @pytest.mark.asyncio
    async def test_cascade_deletes_club_members(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        # Sanity check the membership row exists before deletion
        before = await db_session.execute(
            select(ClubMember).where(ClubMember.club_id == club.id)
        )
        assert len(before.scalars().all()) == 1

        await delete_club(session=db_session, organization_id=org.id, club_id=club.id)

        after = await db_session.execute(
            select(ClubMember).where(ClubMember.club_id == club.id)
        )
        assert after.scalars().all() == []

    @pytest.mark.asyncio
    async def test_member_user_is_not_deleted(self, db_session: AsyncSession):
        """Deleting a club must not delete the Users who were members —
        clubs are optional affinity groups, users just lose membership."""
        org, owner, branch, club = await _make_club_with_owner(db_session)

        await delete_club(session=db_session, organization_id=org.id, club_id=club.id)

        result = await db_session.execute(select(User).where(User.id == owner.id))
        assert result.scalar_one_or_none() is not None


class TestDeleteClubNotFound:
    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, _owner = await make_org_with_owner(db_session)

        with pytest.raises(AppError) as exc_info:
            await delete_club(
                session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_club_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org_a, owner_a = await make_org_with_owner(
            db_session, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, owner_b, branch_b, club_b = await _make_club_with_owner(
            db_session,
            org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )

        with pytest.raises(AppError) as exc_info:
            await delete_club(
                session=db_session,
                organization_id=org_a.id,
                club_id=club_b.id,
            )

        assert exc_info.value.status_code == 404

        # And the club must still exist, untouched, under its real org
        result = await db_session.execute(select(Club).where(Club.id == club_b.id))
        assert result.scalar_one_or_none() is not None