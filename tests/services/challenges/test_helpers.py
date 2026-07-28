"""
Tests for app/services/challenge/_helpers.py

Coverage:
 - get_challenge_or_404: retrieval and 404 cases
 - validate_org_and_branch: org existence, branch existence, cross-org validation
 - check_challenge_visibility: org-wide vs branch-scoped, super_admin bypass, branch membership
 - check_participation_eligibility: same as visibility
 - check_challenge_completion: cumulative sum logic, daily_minimum logic
 - mark_challenge_complete: sets completed_at, creates notification
 - notify_daily_goal_met: deduplication logic, notification creation
"""

from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Challenge, ChallengeParticipant, ChallengeProgressEntry, Branch, Organization
from app.services.challenge._helpers import (
   get_challenge_or_404,
   validate_org_and_branch,
   check_challenge_visibility,
   check_participation_eligibility,
   check_challenge_completion,
   mark_challenge_complete,
   notify_daily_goal_met,
)
from tests.conftest import make_org_with_owner, make_user_with_role


# ── Factories ─────────────────────────────────────────────────────────────────

def make_challenge(
   organization_id: str,
   branch_id: str | None = None,
   name: str = "Test Challenge",
   target_type: str = "cumulative",
   target_value: int | None = 10000,
   **overrides,
) -> Challenge:
   defaults = dict(
       name=name,
       description="A test challenge",
       start_date=date(2025, 1, 1),
       end_date=date(2025, 1, 31),
       target_type=target_type,
       target_value=target_value,
       created_by="test-user-id",
   )
   return Challenge(
       organization_id=organization_id,
       branch_id=branch_id,
       **{**defaults, **overrides},
   )


def make_participant(challenge_id: str, user_id: str, **overrides) -> ChallengeParticipant:
   defaults = dict(
       joined_at=datetime.now(timezone.utc),
       completed_at=None,
   )
   return ChallengeParticipant(
       challenge_id=challenge_id,
       user_id=user_id,
       **{**defaults, **overrides},
   )


def make_progress_entry(
   challenge_id: str,
   user_id: str,
   value: int,
   entry_date: date | None = None,
   **overrides,
) -> ChallengeProgressEntry:
   defaults = dict(
       entry_date=entry_date or date.today(),
   )
   return ChallengeProgressEntry(
       challenge_id=challenge_id,
       user_id=user_id,
       value=value,
       **{**defaults, **overrides},
   )


# ── get_challenge_or_404 ─────────────────────────────────────────────────────

class TestGetChallengeOr404:
   @pytest.mark.asyncio
   async def test_returns_challenge_when_found(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, name="Found Challenge")
       db_session.add(challenge)
       await db_session.flush()

       result = await get_challenge_or_404(
           session=db_session, organization_id=org.id, challenge_id=challenge.id
       )

       assert result.id == challenge.id
       assert result.name == "Found Challenge"

   @pytest.mark.asyncio
   async def test_raises_404_when_challenge_not_found(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)

       with pytest.raises(HTTPException) as exc_info:
           await get_challenge_or_404(
               session=db_session,
               organization_id=org.id,
               challenge_id="00000000-0000-0000-0000-000000000000",
           )

       assert exc_info.value.status_code == 404
       assert "Challenge not found" in exc_info.value.detail

   @pytest.mark.asyncio
   async def test_raises_404_when_challenge_belongs_to_different_org(
       self, db_session: AsyncSession
   ):
       org_a, owner_a = await make_org_with_owner(
           db_session, org_kwargs={"slug": "org-a"}, user_kwargs={"email": "a@example.com"}
       )
       org_b, owner_b = await make_org_with_owner(
           db_session, org_kwargs={"slug": "org-b"}, user_kwargs={"email": "b@example.com"}
       )

       challenge = make_challenge(org_a.id, name="Org A Challenge")
       db_session.add(challenge)
       await db_session.flush()

       with pytest.raises(HTTPException) as exc_info:
           await get_challenge_or_404(
               session=db_session, organization_id=org_b.id, challenge_id=challenge.id
           )

       assert exc_info.value.status_code == 404


# ── validate_org_and_branch ──────────────────────────────────────────────────

class TestValidateOrgAndBranch:
   @pytest.mark.asyncio
   async def test_passes_when_org_exists_no_branch(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)

       # Should not raise
       await validate_org_and_branch(
           session=db_session, organization_id=org.id, branch_id=None
       )

   @pytest.mark.asyncio
   async def test_passes_when_org_and_branch_valid(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       # owner.branch_id is the HQ branch from make_org_with_owner

       await validate_org_and_branch(
           session=db_session, organization_id=org.id, branch_id=owner.branch_id
       )

   @pytest.mark.asyncio
   async def test_raises_404_when_org_not_found(self, db_session: AsyncSession):
       with pytest.raises(HTTPException) as exc_info:
           await validate_org_and_branch(
               session=db_session,
               organization_id="00000000-0000-0000-0000-000000000000",
               branch_id=None,
           )

       assert exc_info.value.status_code == 404
       assert "Organization not found" in exc_info.value.detail

   @pytest.mark.asyncio
   async def test_raises_404_when_branch_not_in_org(self, db_session: AsyncSession):
       org_a, owner_a = await make_org_with_owner(
           db_session, org_kwargs={"slug": "org-a"}, user_kwargs={"email": "a@example.com"}
       )
       org_b, owner_b = await make_org_with_owner(
           db_session, org_kwargs={"slug": "org-b"}, user_kwargs={"email": "b@example.com"}
       )

       with pytest.raises(HTTPException) as exc_info:
           await validate_org_and_branch(
               session=db_session,
               organization_id=org_a.id,
               branch_id=owner_b.branch_id,  # Branch from org_b
           )

       assert exc_info.value.status_code == 404
       assert "Branch not found" in exc_info.value.detail


# ── check_challenge_visibility ───────────────────────────────────────────────

class TestCheckChallengeVisibility:
   @pytest.mark.asyncio
   async def test_passes_for_org_wide_challenge(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, branch_id=None, name="Org Wide Challenge")
       db_session.add(challenge)
       await db_session.flush()

       # Mock current_user
       class MockUser:
           branch_id = "some-other-branch"
           role = "employee"

       # Should not raise for org-wide
       check_challenge_visibility(current_user=MockUser(), challenge=challenge)

   @pytest.mark.asyncio
   async def test_passes_when_user_in_same_branch(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, branch_id=owner.branch_id, name="Branch Challenge")
       db_session.add(challenge)
       await db_session.flush()

       class MockUser:
           branch_id = owner.branch_id
           role = "employee"

       check_challenge_visibility(current_user=MockUser(), challenge=challenge)

   @pytest.mark.asyncio
   async def test_passes_for_super_admin_any_branch(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       
       # Create a different branch
       from app.models.branch import Branch
       other_branch = Branch(organization_id=org.id, name="Other Branch")
       db_session.add(other_branch)
       await db_session.flush()

       challenge = make_challenge(org.id, branch_id=other_branch.id, name="Other Branch Challenge")
       db_session.add(challenge)
       await db_session.flush()

       class MockUser:
           branch_id = owner.branch_id  # Different from challenge branch
           role = "super_admin"

       # Super admin can see any branch
       check_challenge_visibility(current_user=MockUser(), challenge=challenge)

   @pytest.mark.asyncio
   async def test_raises_403_when_user_in_different_branch(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       
       from app.models.branch import Branch
       other_branch = Branch(organization_id=org.id, name="Other Branch")
       db_session.add(other_branch)
       await db_session.flush()

       challenge = make_challenge(org.id, branch_id=other_branch.id, name="Other Branch Challenge")
       db_session.add(challenge)
       await db_session.flush()

       class MockUser:
           branch_id = owner.branch_id  # Different branch
           role = "employee"

       with pytest.raises(HTTPException) as exc_info:
           check_challenge_visibility(current_user=MockUser(), challenge=challenge)

       assert exc_info.value.status_code == 403


# ── check_participation_eligibility ──────────────────────────────────────────

class TestCheckParticipationEligibility:
   @pytest.mark.asyncio
   async def test_uses_same_logic_as_visibility(self, db_session: AsyncSession):
       """Participation eligibility delegates to visibility check."""
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, branch_id=None)
       db_session.add(challenge)
       await db_session.flush()

       class MockUser:
           branch_id = "any-branch"
           role = "employee"

       # Should not raise for org-wide
       check_participation_eligibility(current_user=MockUser(), challenge=challenge)


# ── check_challenge_completion ───────────────────────────────────────────────

class TestCheckChallengeCompletion:
   @pytest.mark.asyncio
   async def test_cumulative_completes_when_total_reaches_target(
       self, db_session: AsyncSession
   ):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(
           org.id, target_type="cumulative", target_value=100, branch_id=None
       )
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       # Add progress entries totaling 100
       db_session.add(make_progress_entry(challenge.id, owner.id, 40))
       db_session.add(make_progress_entry(challenge.id, owner.id, 60))
       await db_session.flush()

       with patch(
           "app.services.challenge._helpers.mark_challenge_complete", new=AsyncMock()
       ) as mock_complete:
           await check_challenge_completion(
               session=db_session, challenge=challenge, participant=participant
           )
           mock_complete.assert_called_once()

   @pytest.mark.asyncio
   async def test_cumulative_does_not_complete_below_target(
       self, db_session: AsyncSession
   ):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(
           org.id, target_type="cumulative", target_value=1000, branch_id=None
       )
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       db_session.add(make_progress_entry(challenge.id, owner.id, 100))
       await db_session.flush()

       with patch(
           "app.services.challenge._helpers.mark_challenge_complete", new=AsyncMock()
       ) as mock_complete:
           await check_challenge_completion(
               session=db_session, challenge=challenge, participant=participant
           )
           mock_complete.assert_not_called()

   @pytest.mark.asyncio
   async def test_daily_minimum_notifies_when_today_meets_target(
       self, db_session: AsyncSession
   ):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(
           org.id, target_type="daily_minimum", target_value=5000, branch_id=None
       )
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       # Today's entry meets target
       db_session.add(make_progress_entry(challenge.id, owner.id, 5000, date.today()))
       await db_session.flush()

       with patch(
           "app.services.challenge._helpers.notify_daily_goal_met", new=AsyncMock()
       ) as mock_notify:
           await check_challenge_completion(
               session=db_session, challenge=challenge, participant=participant
           )
           mock_notify.assert_called_once()

   @pytest.mark.asyncio
   async def test_no_action_when_target_value_is_none(self, db_session: AsyncSession):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(
           org.id, target_type="cumulative", target_value=None, branch_id=None
       )
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       # Should not raise or call anything
       await check_challenge_completion(
           session=db_session, challenge=challenge, participant=participant
       )


# ── mark_challenge_complete ─────────────────────────────────────────────────

class TestMarkChallengeComplete:
   @pytest.mark.asyncio
   async def test_sets_completed_at_and_creates_notification(
       self, db_session: AsyncSession
   ):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, name="Completion Challenge", branch_id=None)
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       with patch(
           "app.services.challenge._helpers.create_notification", new=AsyncMock()
       ) as mock_notify:
           await mark_challenge_complete(
               session=db_session, challenge=challenge, participant=participant
           )

           assert participant.completed_at is not None
           mock_notify.assert_called_once()


# ── notify_daily_goal_met ─────────────────────────────────────────────────────

class TestNotifyDailyGoalMet:
   @pytest.mark.asyncio
   async def test_creates_notification_when_none_exists_today(
       self, db_session: AsyncSession
   ):
       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, name="Daily Challenge", branch_id=None)
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       with patch(
           "app.services.challenge._helpers.create_notification", new=AsyncMock()
       ) as mock_notify:
           await notify_daily_goal_met(
               session=db_session,
               challenge=challenge,
               participant=participant,
               entry_date=date.today(),
           )

           mock_notify.assert_called_once()
           call_kwargs = mock_notify.call_args.kwargs
           assert call_kwargs["notification_type"] == "challenge_daily_goal_met"

   @pytest.mark.asyncio
   async def test_skips_when_notification_already_exists_today(
       self, db_session: AsyncSession
   ):
       from app.models import Notification

       org, owner = await make_org_with_owner(db_session)
       challenge = make_challenge(org.id, name="Daily Challenge", branch_id=None)
       db_session.add(challenge)
       await db_session.flush()

       participant = make_participant(challenge.id, owner.id)
       db_session.add(participant)
       await db_session.flush()

       # Create existing notification from today
       existing = Notification(
           user_id=owner.id,
           organization_id=org.id,
           notification_type="challenge_daily_goal_met",
           reference_id=challenge.id,
           reference_type="challenge",
           body="Already notified",
           created_at=datetime.now(timezone.utc),
       )
       db_session.add(existing)
       await db_session.flush()

       with patch(
           "app.services.challenge._helpers.create_notification", new=AsyncMock()
       ) as mock_notify:
           await notify_daily_goal_met(
               session=db_session,
               challenge=challenge,
               participant=participant,
               entry_date=date.today(),
           )

           mock_notify.assert_not_called()