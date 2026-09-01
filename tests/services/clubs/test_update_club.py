"""
tests/services/clubs/test_update_club.py

Tests for app/services/club/update_club.py

Coverage:
  - Happy path: name, description, privacy each updatable independently
  - Partial update: fields left as None are not touched
  - Name uniqueness check excludes the club's own current row
  - Name uniqueness check still rejects a collision with a DIFFERENT club
  - name.strip() applied on update
  - branch_id is not an accepted parameter — cannot be changed via this
    function's signature (structural guarantee, not just behavioral)
  - 404 when club doesn't exist
  - 404 when club belongs to a different org
"""

from __future__ import annotations

import inspect

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models import SubscriptionPlan, OrganizationSubscription
from app.services.club.create_club import create_club
from app.services.club.update_club import edit_club
from app.services.errors import AppError

from tests.conftest import make_org_with_owner


async def _make_branch(db_session: AsyncSession, organization_id: str, **overrides) -> Branch:
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


async def _make_club_with_owner(db_session: AsyncSession, *, org_kwargs=None, user_kwargs=None, **club_overrides):
    """Sets up an org + branch-assigned owner + an active subscription
    (required by create_club()'s plan-limit check), then creates a club
    via the real create_club() service."""
    org, owner = await make_org_with_owner(db_session, with_trial=False, org_kwargs=org_kwargs, user_kwargs=user_kwargs)
    branch = await _make_branch(db_session, org.id)
    owner.branch_id = branch.id
    await db_session.flush()

    plan = await _make_plan(db_session)
    await _make_subscription(db_session, org.id, plan.id)

    defaults = dict(
        organization_id=org.id,
        branch_id=branch.id,
        name="Running Club",
        description="Weekly run",
        privacy="public",
        created_by=owner.id,
    )
    club = await create_club(image_url=None, category="fitness", session=db_session, **{**defaults, **club_overrides})
    return org, owner, branch, club


class TestEditClubHappyPath:
    @pytest.mark.asyncio
    async def test_updates_name(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name="Sprint Club",
            description=None,
            privacy=None,
        )

        assert updated.name == "Sprint Club"

    @pytest.mark.asyncio
    async def test_updates_description(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name=None,
            description="New description",
            privacy=None,
        )

        assert updated.description == "New description"

    @pytest.mark.asyncio
    async def test_updates_privacy(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session, privacy="public")

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name=None,
            description=None,
            privacy="private",
        )

        assert updated.privacy == "private"

    @pytest.mark.asyncio
    async def test_updates_all_fields_together(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name="New Name",
            description="New description",
            privacy="private",
        )

        assert updated.name == "New Name"
        assert updated.description == "New description"
        assert updated.privacy == "private"

    @pytest.mark.asyncio
    async def test_name_is_stripped(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(db_session)

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name="   Padel Club   ",
            description=None,
            privacy=None,
        )

        assert updated.name == "Padel Club"


class TestEditClubPartialUpdate:
    @pytest.mark.asyncio
    async def test_none_fields_are_left_untouched(self, db_session: AsyncSession):
        org, owner, branch, club = await _make_club_with_owner(
            db_session, name="Original Name", description="Original description", privacy="public"
        )

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name=None,
            description=None,
            privacy=None,
        )

        assert updated.name == "Original Name"
        assert updated.description == "Original description"
        assert updated.privacy == "public"

    @pytest.mark.asyncio
    async def test_branch_id_is_not_an_accepted_parameter(self):
        """
        Structural guarantee, not just behavioral: edit_club()'s
        signature has no branch_id parameter at all, so a club cannot
        be moved between branches through this function no matter what
        a caller tries to pass — there's no path through which to even
        attempt it.
        """
        params = inspect.signature(edit_club).parameters
        assert "branch_id" not in params


class TestEditClubNameUniqueness:
    @pytest.mark.asyncio
    async def test_allows_setting_name_to_its_own_current_value(
        self, db_session: AsyncSession
    ):
        """
        The uniqueness check excludes the club's own id (Club.id !=
        club_id), so re-submitting the same name it already has must
        not raise a false 409.
        """
        org, owner, branch, club = await _make_club_with_owner(db_session, name="Running Club")

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            club_id=club.id,
            name="Running Club",
            description=None,
            privacy=None,
        )

        assert updated.name == "Running Club"

    @pytest.mark.asyncio
    async def test_raises_409_when_name_collides_with_a_different_club(
        self, db_session: AsyncSession
    ):
        org, owner, branch, club_a = await _make_club_with_owner(
            db_session, name="Running Club"
        )
        club_b = await create_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Book Club",
            description=None,
            privacy="public",
            created_by=owner.id,
        )

        with pytest.raises(AppError) as exc_info:
            await edit_club(
                image_url=None, category="fitness", session=db_session,
                organization_id=org.id,
                club_id=club_b.id,
                name="Running Club",
                description=None,
                privacy=None,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_name_collision_check_is_scoped_to_own_org(
        self, db_session: AsyncSession
    ):
        """A name collision in a different org must not block the update."""
        org_a, owner_a, branch_a, club_a = await _make_club_with_owner(
            db_session,
            org_kwargs={"business_name": "Org A", "slug": "org-a"},
            name="Shared Name",
        )
        org_b, owner_b, branch_b, club_b = await _make_club_with_owner(
            db_session,
            org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
            name="Different Name",
        )

        updated = await edit_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org_b.id,
            club_id=club_b.id,
            name="Shared Name",
            description=None,
            privacy=None,
        )

        assert updated.name == "Shared Name"


class TestEditClubNotFound:
    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session, with_trial=False)

        with pytest.raises(AppError) as exc_info:
            await edit_club(
                image_url=None, category="fitness", session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
                name="Doesn't Matter",
                description=None,
                privacy=None,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_club_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org_a, owner_a = await make_org_with_owner(
            db_session, with_trial=False, org_kwargs={"business_name": "Org A", "slug": "org-a"}
        )
        org_b, owner_b, branch_b, club_b = await _make_club_with_owner(
            db_session,
            org_kwargs={"business_name": "Org B", "slug": "org-b"},
            user_kwargs={"email": "owner-b@example.com"},
        )

        with pytest.raises(AppError) as exc_info:
            await edit_club(
                image_url=None, category="fitness", session=db_session,
                organization_id=org_a.id,
                club_id=club_b.id,
                name="Hijacked Name",
                description=None,
                privacy=None,
            )

        assert exc_info.value.status_code == 404

        # Confirm it was untouched
        await db_session.refresh(club_b)
        assert club_b.name != "Hijacked Name"