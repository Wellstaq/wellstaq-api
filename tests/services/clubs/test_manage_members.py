"""
tests/services/clubs/test_manage_members.py

Tests for app/services/club/manage_members.py

Coverage:
  join_club:
    - Happy path join, member_count increments
    - 404 when club doesn't exist
    - 403 when club is private
    - 403 when user isn't in the club's branch
    - 409 when already a member
    - a user can join multiple different clubs

  leave_club:
    - Happy path leave, member_count decrements
    - 404 when club doesn't exist
    - 409 when the leader tries to leave
    - 404 when user isn't actually a member
    - member_count never goes below 0

  remove_club_member:
    - Happy path removal by the leader
    - 404 when club doesn't exist
    - 400 when leader tries to remove themselves
    - 404 when target isn't a member
    (requester-authorization is enforced by the router, not this
    service — see remove_club_member_route for that coverage)

  get_club_members:
    - Returns paginated members ordered by first_name
    - total reflects full member count, not page size
    - 404 when club doesn't exist
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.club import Club, ClubMember
from app.models import SubscriptionPlan, OrganizationSubscription
from app.services.club.create_club import create_club
from app.services.club.manage_members import (
    get_club_members,
    join_club,
    leave_club,
    remove_club_member,
)
from app.services.errors import AppError

from tests.conftest import make_org_with_owner, make_user_with_role


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


async def _setup(db_session: AsyncSession, *, privacy: str = "public"):
    """Standard fixture: org, branch, leader (owner) with a club they
    created, and one additional branch-member user who is NOT yet in
    the club — the typical starting point for join/leave/remove tests.
    Also sets up an active subscription, required by create_club()'s
    plan-limit check."""
    org, leader = await make_org_with_owner(db_session, with_trial=False)
    branch = await _make_branch(db_session, org.id)
    leader.branch_id = branch.id
    await db_session.flush()

    plan = await _make_plan(db_session)
    await _make_subscription(db_session, org.id, plan.id)

    club = await create_club(
        image_url=None, category="fitness", session=db_session,
        organization_id=org.id,
        branch_id=branch.id,
        name="Running Club",
        description=None,
        privacy=privacy,
        created_by=leader.id,
    )

    member = await make_user_with_role(
        db_session,
        organization_id=org.id,
        role_name="member",
        email="member@example.com",
        branch_id=branch.id,
    )
    db_session.add(member)
    await db_session.flush()

    return org, branch, leader, club, member


class TestJoinClub:
    @pytest.mark.asyncio
    async def test_adds_member_and_increments_count(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        await join_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        await db_session.refresh(club)
        assert club.member_count == 2  # leader + new member

    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        with pytest.raises(AppError) as exc_info:
            await join_club(
                session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
                user_id=member.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_when_club_is_private(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session, privacy="private")

        with pytest.raises(AppError) as exc_info:
            await join_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_403_when_user_not_in_clubs_branch(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        other_branch = await _make_branch(db_session, org.id, name="Other Branch")
        member.branch_id = other_branch.id
        await db_session.flush()

        with pytest.raises(AppError) as exc_info:
            await join_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_409_when_already_a_member(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        await join_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        with pytest.raises(AppError) as exc_info:
            await join_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_user_can_join_multiple_clubs(self, db_session: AsyncSession):
        org, branch, leader, club_a, member = await _setup(db_session)
        club_b = await create_club(
            image_url=None, category="fitness", session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Book Club",
            description=None,
            privacy="public",
            created_by=leader.id,
        )

        await join_club(
            session=db_session, organization_id=org.id, club_id=club_a.id, user_id=member.id
        )
        await join_club(
            session=db_session, organization_id=org.id, club_id=club_b.id, user_id=member.id
        )

        members_a, _ = await get_club_members(
            session=db_session, organization_id=org.id, club_id=club_a.id
        )
        members_b, _ = await get_club_members(
            session=db_session, organization_id=org.id, club_id=club_b.id
        )
        assert member.id in [m.id for m in members_a]
        assert member.id in [m.id for m in members_b]


class TestLeaveClub:
    @pytest.mark.asyncio
    async def test_removes_member_and_decrements_count(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        await join_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        await leave_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        await db_session.refresh(club)
        assert club.member_count == 1  # only leader remains

    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        with pytest.raises(AppError) as exc_info:
            await leave_club(
                session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
                user_id=member.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_leader_tries_to_leave(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        with pytest.raises(AppError) as exc_info:
            await leave_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=leader.id
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_a_member(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        # member never joined

        with pytest.raises(AppError) as exc_info:
            await leave_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_member_count_never_goes_below_zero(self, db_session: AsyncSession):
        """
        Defends the max(0, ...) floor in leave_club(). Under normal
        flow member_count can't legitimately go negative (you can't
        leave without being a member first), but this locks in the
        floor behavior in case member_count and ClubMember rows ever
        drift out of sync (e.g. manual DB edits, a future bug elsewhere).
        """
        org, branch, leader, club, member = await _setup(db_session)
        await join_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )
        club.member_count = 0  # simulate drift
        await db_session.flush()

        await leave_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        await db_session.refresh(club)
        assert club.member_count == 0


class TestRemoveClubMember:
    @pytest.mark.asyncio
    async def test_leader_can_remove_a_member(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        await join_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        await remove_club_member(
            session=db_session,
            organization_id=org.id,
            club_id=club.id,
            target_user_id=member.id,
            requesting_user_id=leader.id,
        )

        await db_session.refresh(club)
        assert club.member_count == 1

    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        with pytest.raises(AppError) as exc_info:
            await remove_club_member(
                session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
                target_user_id=member.id,
                requesting_user_id=leader.id,
            )

        assert exc_info.value.status_code == 404

    # NOTE: "requester is not the leader" is NOT tested here.
    # remove_club_member()'s docstring is explicit that authorization
    # (leader, or club.update permission) is enforced by the router
    # (remove_club_member_route) before this function is ever called —
    # the service itself never checks requesting_user_id against
    # club.leader_id or any permission. That coverage belongs in a
    # router-level test against remove_club_member_route instead.

    @pytest.mark.asyncio
    async def test_raises_400_when_leader_removes_self(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        with pytest.raises(AppError) as exc_info:
            await remove_club_member(
                session=db_session,
                organization_id=org.id,
                club_id=club.id,
                target_user_id=leader.id,
                requesting_user_id=leader.id,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_404_when_target_not_a_member(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        # member never joined

        with pytest.raises(AppError) as exc_info:
            await remove_club_member(
                session=db_session,
                organization_id=org.id,
                club_id=club.id,
                target_user_id=member.id,
                requesting_user_id=leader.id,
            )

        assert exc_info.value.status_code == 404


class TestGetClubMembers:
    @pytest.mark.asyncio
    async def test_raises_404_when_club_does_not_exist(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)

        with pytest.raises(AppError) as exc_info:
            await get_club_members(
                session=db_session,
                organization_id=org.id,
                club_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_all_members_including_leader(self, db_session: AsyncSession):
        org, branch, leader, club, member = await _setup(db_session)
        await join_club(
            session=db_session, organization_id=org.id, club_id=club.id, user_id=member.id
        )

        members, total = await get_club_members(
            session=db_session, organization_id=org.id, club_id=club.id
        )

        assert total == 2
        assert {m.id for m in members} == {leader.id, member.id}

    @pytest.mark.asyncio
    async def test_pagination(self, db_session: AsyncSession):
        org, branch, leader, club, _first_member = await _setup(db_session)
        # leader is already 1 member; add 3 more with distinct first names
        for name in ["Amy", "Bob", "Cara"]:
            extra = await make_user_with_role(
                db_session,
                organization_id=org.id,
                role_name="member",
                email=f"{name.lower()}@example.com",
                first_name=name,
                branch_id=branch.id,
            )
            db_session.add(extra)
            await db_session.flush()
            await join_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=extra.id
            )

        page_1, total = await get_club_members(
            session=db_session, organization_id=org.id, club_id=club.id, offset=0, limit=2
        )

        assert total == 4  # leader + 3 extras
        assert len(page_1) == 2

    @pytest.mark.asyncio
    async def test_ordered_by_first_name(self, db_session: AsyncSession):
        org, branch, leader, club, _first_member = await _setup(db_session)
        for name in ["Zara", "Amy"]:
            extra = await make_user_with_role(
                db_session,
                organization_id=org.id,
                role_name="member",
                email=f"{name.lower()}@example.com",
                first_name=name,
                branch_id=branch.id,
            )
            db_session.add(extra)
            await db_session.flush()
            await join_club(
                session=db_session, organization_id=org.id, club_id=club.id, user_id=extra.id
            )

        members, _ = await get_club_members(
            session=db_session, organization_id=org.id, club_id=club.id
        )

        names = [m.first_name for m in members]
        assert names == sorted(names)