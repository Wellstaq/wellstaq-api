"""
tests/services/clubs/test_create_club.py

Tests for app/services/club/create_club.py

Scope note: create_club() assumes the caller is already authorized (see
its docstring) — branch-membership / club.create permission checks live
in the router (create_club_route), not here. That authorization is
covered separately in the router-level tests
(tests/routers/test_clubs.py::TestCreateClubRouteAuthorization), not in
this file.

Coverage:
  - Happy path: club created, creator becomes leader + first member
  - Branch must belong to the same organization
  - Club name uniqueness is enforced org-wide
  - member_count and ClubMember row are correct after creation
  - Plan limits (max_clubs) are enforced
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, ClubMember, Branch, SubscriptionPlan, OrganizationSubscription
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


async def _make_plan(db_session: AsyncSession, **overrides) -> SubscriptionPlan:
    """Create a subscription plan with generous limits for testing."""
    defaults = dict(
        name="Test Plan",
        billing_interval="monthly",
        amount=0,
        currency="USD",
        features={"max_clubs": None},  # Unlimited clubs for testing
    )
    plan = SubscriptionPlan(**{**defaults, **overrides})
    db_session.add(plan)
    await db_session.flush()
    return plan


async def _make_subscription(
    db_session: AsyncSession,
    organization_id: str,
    plan_id: str,
    **overrides
) -> OrganizationSubscription:
    """Create an active subscription for an organization."""
    defaults = dict(
        status="active",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    sub = OrganizationSubscription(
        organization_id=organization_id,
        plan_id=plan_id,
        **{**defaults, **overrides}
    )
    db_session.add(sub)
    await db_session.flush()
    return sub


async def _setup_org_with_subscription(db_session: AsyncSession, **org_kwargs):
    """Create org + owner + branch + plan + subscription. Returns (org, owner, branch, plan)."""
    org, owner = await make_org_with_owner(db_session, with_trial=False, **org_kwargs)
    branch = await _make_branch(db_session, org.id)
    owner.branch_id = branch.id
    await db_session.flush()

    plan = await _make_plan(db_session)
    await _make_subscription(db_session, org.id, plan.id)

    return org, owner, branch, plan


class TestCreateClubHappyPath:
    @pytest.mark.asyncio
    async def test_creates_club_with_correct_fields(self, db_session: AsyncSession):
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        club = await create_club(
            image_url=None, category="fitness", session=db_session,
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
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        club = await create_club(
            image_url=None, category="fitness", session=db_session,
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
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        club = await create_club(
            image_url=None, category="fitness", session=db_session,
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
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        club = await create_club(
            image_url=None, category="fitness", session=db_session,
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
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        club = await create_club(
            image_url=None, category="fitness", session=db_session,
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
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        # Create subscription even though branch check will fail first
        plan = await _make_plan(db_session)
        await _make_subscription(db_session, org.id, plan.id)

        with pytest.raises(AppError) as exc_info:
            await create_club(
                image_url=None, category="fitness", session=db_session,
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
            db_session, with_trial=False, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, _ = await make_org_with_owner(
            db_session,
            with_trial=False, org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )
        # Create subscriptions for both orgs
        plan = await _make_plan(db_session)
        await _make_subscription(db_session, org_a.id, plan.id)
        await _make_subscription(db_session, org_b.id, plan.id)

        branch_b = await _make_branch(db_session, org_b.id)

        with pytest.raises(AppError) as exc_info:
            await create_club(
                image_url=None, category="fitness", session=db_session,
                organization_id=org_a.id,
                branch_id=branch_b.id,
                name="Cross Org Club",
                description=None,
                privacy="public",
                created_by=owner_a.id,
            )

        assert exc_info.value.status_code == 404


class TestCreateClubNameUniqueness:
    @pytest.mark.asyncio
    async def test_raises_409_on_duplicate_name_same_org(self, db_session: AsyncSession):
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        await create_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Yoga Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        with pytest.raises(AppError) as exc_info:
            await create_club(
                image_url=None, category="fitness", session=db_session,
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
        org, owner, branch, plan = await _setup_org_with_subscription(db_session)

        await create_club(
            image_url=None, category="fitness", session=db_session,
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
                image_url=None, category="fitness", session=db_session,
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
            db_session, with_trial=False, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, owner_b = await make_org_with_owner(
            db_session,
            with_trial=False, org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )
        branch_a = await _make_branch(db_session, org_a.id)
        branch_b = await _make_branch(db_session, org_b.id)
        owner_a.branch_id = branch_a.id
        owner_b.branch_id = branch_b.id
        await db_session.flush()

        # Create subscriptions for both orgs
        plan = await _make_plan(db_session)
        await _make_subscription(db_session, org_a.id, plan.id)
        await _make_subscription(db_session, org_b.id, plan.id)

        club_a = await create_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org_a.id,
            branch_id=branch_a.id,
            name="Shared Name Club",
            description=None,
            privacy="public",
            created_by=owner_a.id,
        )
        club_b = await create_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org_b.id,
            branch_id=branch_b.id,
            name="Shared Name Club",
            description=None,
            privacy="public",
            created_by=owner_b.id,
        )

        assert club_a.id != club_b.id
        assert club_a.organization_id != club_b.organization_id


class TestCreateClubPlanLimits:
    @pytest.mark.asyncio
    async def test_raises_402_when_club_limit_reached(self, db_session: AsyncSession):
        """enforce_plan_limit blocks club creation once current_count has
        reached the plan's max_clubs. 402 Payment Required is the correct
        status code for plan-limit rejections."""
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        branch = await _make_branch(db_session, org.id)
        owner.branch_id = branch.id
        await db_session.flush()

        # Create plan with max_clubs=1
        plan = await _make_plan(db_session, features={"max_clubs": 1})
        await _make_subscription(db_session, org.id, plan.id)

        # First club: 0 existing < limit of 1 -> should succeed
        await create_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="First Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        # Second club: 1 existing >= limit of 1 -> should fail with 402
        with pytest.raises(HTTPException) as exc_info:
            await create_club(
                image_url=None, category="fitness", session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="Second Club",
                description=None,
                privacy="public",
                created_by=owner.id,
            )

        assert exc_info.value.status_code == 402
        assert "allows up to 1" in exc_info.value.detail