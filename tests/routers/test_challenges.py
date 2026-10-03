"""
HTTP route tests for app/routers/challenges.py.

All challenge endpoints are exercised through FastAPI's ASGI client with the
real database, authentication, subscription, permission, service, transaction,
and response-schema layers.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Challenge, ChallengeParticipant, ChallengeProgressEntry
from tests.conftest import authenticate_user, make_org_with_owner, make_user


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _authorized_org(
    db_session: AsyncSession,
    *,
    role_name: str = "super_admin",
    permissions: tuple[str, ...] = (),
):
    org, user = await make_org_with_owner(
        db_session, user_kwargs={"role_name": role_name}
    )
    headers, _ = await authenticate_user(
        db_session,
        user,
        permissions=permissions,
        device_fingerprint=f"challenge-router-{user.id}",
    )
    return org, user, headers


async def _make_challenge(
    db_session: AsyncSession,
    organization_id: str,
    *,
    branch_id: str | None = None,
    name: str = "Daily Steps",
    created_by: str | None = None,
    **overrides,
) -> Challenge:
    values = dict(
        name=name,
        description="Walk every day",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=7),
        status="active",
        metric_type="steps",
        target_type="cumulative",
        target_value=10000,
        organization_id=organization_id,
        branch_id=branch_id,
        created_by=created_by,
    )
    challenge = Challenge(**{**values, **overrides})
    db_session.add(challenge)
    await db_session.flush()
    return challenge


async def _make_participant(
    db_session: AsyncSession,
    challenge: Challenge,
    user_id: str,
    **overrides,
) -> ChallengeParticipant:
    participant = ChallengeParticipant(
        challenge_id=challenge.id,
        user_id=user_id,
        organization_id=challenge.organization_id,
        **overrides,
    )
    db_session.add(participant)
    await db_session.flush()
    return participant


# ── Authentication, tenancy, and permission checks ──────────────────────────

class TestChallengeRouteAccess:
    @pytest.mark.asyncio
    async def test_list_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/challenges"
        )

        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_list_rejects_different_organization(self, client, db_session: AsyncSession):
        _, _, headers = await _authorized_org(db_session)
        other_org, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "other-challenge-org", "business_name": "Other Challenge Org"},
            user_kwargs={"email": "other-challenge-owner@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/challenges", headers=headers
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_requires_challenge_create_permission(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")

        response = await client.post(
            f"/organizations/{org.id}/challenges",
            headers=headers,
            json={
                "branch_id": member.branch_id,
                "name": "No Permission",
                "start_date": date.today().isoformat(),
                "end_date": (date.today() + timedelta(days=1)).isoformat(),
                "target_type": "cumulative",
                "target_value": 100,
            },
        )

        assert response.status_code == 403
        assert "challenge.create" in response.json()["detail"]


# ── Create, list, get, and statistics ────────────────────────────────────────

class TestCreateChallengeRoute:
    @pytest.mark.asyncio
    async def test_creates_upcoming_challenge_and_persists_creator(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/challenges",
            headers=headers,
            json={
                "branch_id": owner.branch_id,
                "name": "  Hydration Week  ",
                "start_date": date.today().isoformat(),
                "end_date": (date.today() + timedelta(days=7)).isoformat(),
                "metric_type": "WATER_LITERS",
                "target_type": "cumulative",
                "target_value": 14,
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Hydration Week"
        assert body["status"] == "upcoming"
        assert body["metric_type"] == "water_liters"
        assert body["created_by"] == owner.id
        stored = (
            await db_session.execute(select(Challenge).where(Challenge.id == body["id"]))
        ).scalar_one()
        assert stored.branch_id == owner.branch_id

    @pytest.mark.asyncio
    async def test_rejects_invalid_target_type(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.create",)
        )

        response = await client.post(
            f"/organizations/{org.id}/challenges",
            headers=headers,
            json={
                "branch_id": owner.branch_id,
                "name": "Invalid",
                "start_date": date.today().isoformat(),
                "end_date": (date.today() + timedelta(days=1)).isoformat(),
                "target_type": "weekly_average",
            },
        )

        assert response.status_code == 422


class TestReadChallengeRoutes:
    @pytest.mark.asyncio
    async def test_list_filters_by_status_and_returns_computed_counts(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        active = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id, name="Active"
        )
        await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id, name="Upcoming", status="upcoming"
        )
        await _make_participant(db_session, active, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/challenges?status=active", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Active"
        assert body["items"][0]["participant_count"] == 1

    @pytest.mark.asyncio
    async def test_branch_member_cannot_list_another_branch(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        other_branch_id = "00000000-0000-0000-0000-000000000123"

        response = await client.get(
            f"/organizations/{org.id}/challenges?branch_id={other_branch_id}",
            headers=headers,
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_returns_visible_challenge(self, client, db_session: AsyncSession):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        challenge = await _make_challenge(
            db_session, org.id, branch_id=member.branch_id
        )

        response = await client.get(
            f"/organizations/{org.id}/challenges/{challenge.id}", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["id"] == challenge.id

    @pytest.mark.asyncio
    async def test_stats_returns_real_aggregates(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(db_session)
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )
        await _make_participant(db_session, challenge, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/challenges/stats?branch_id={owner.branch_id}",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["active_challenges"] == 1
        assert body["total_participants"] == 1
        assert body["completion_rate"] == 0
        assert body["active_challenges_trend"] is None

    @pytest.mark.asyncio
    async def test_custom_stats_requires_start_date(self, client, db_session: AsyncSession):
        org, _, headers = await _authorized_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/challenges/stats?period=custom", headers=headers
        )

        assert response.status_code == 400


# ── Challenge mutations ──────────────────────────────────────────────────────

class TestUpdateCancelDeleteChallengeRoutes:
    @pytest.mark.asyncio
    async def test_update_persists_changes(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.update",)
        )
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )

        response = await client.patch(
            f"/organizations/{org.id}/challenges/{challenge.id}",
            headers=headers,
            json={"name": "Updated Challenge", "target_value": 25000},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Updated Challenge"
        await db_session.refresh(challenge)
        assert challenge.name == "Updated Challenge"

    @pytest.mark.asyncio
    async def test_cancel_preserves_record_and_sets_status(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.update",)
        )
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )

        response = await client.post(
            f"/organizations/{org.id}/challenges/{challenge.id}/cancel",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        await db_session.refresh(challenge)
        assert challenge.status == "cancelled"

    @pytest.mark.asyncio
    async def test_delete_removes_challenge(self, client, db_session: AsyncSession):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.delete",)
        )
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )
        challenge_id = challenge.id

        response = await client.delete(
            f"/organizations/{org.id}/challenges/{challenge_id}", headers=headers
        )

        assert response.status_code == 200
        assert response.json() == {"success": True}
        assert (
            await db_session.execute(select(Challenge).where(Challenge.id == challenge_id))
        ).scalar_one_or_none() is None


# ── Participation and progress ───────────────────────────────────────────────

class TestChallengeParticipationRoutes:
    @pytest.mark.asyncio
    async def test_join_and_leave_persist_participation(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        challenge = await _make_challenge(
            db_session, org.id, branch_id=member.branch_id
        )

        joined = await client.post(
            f"/organizations/{org.id}/challenges/{challenge.id}/join", headers=headers
        )
        assert joined.status_code == 201
        assert joined.json()["user_id"] == member.id

        left = await client.delete(
            f"/organizations/{org.id}/challenges/{challenge.id}/join", headers=headers
        )
        assert left.status_code == 200
        assert left.json() == {"success": True}
        assert (
            await db_session.execute(
                select(ChallengeParticipant).where(
                    ChallengeParticipant.challenge_id == challenge.id,
                    ChallengeParticipant.user_id == member.id,
                )
            )
        ).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_duplicate_join_returns_conflict(self, client, db_session: AsyncSession):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        challenge = await _make_challenge(
            db_session, org.id, branch_id=member.branch_id
        )
        await _make_participant(db_session, challenge, member.id)

        response = await client.post(
            f"/organizations/{org.id}/challenges/{challenge.id}/join", headers=headers
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_list_participants_is_paginated_and_safe(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(db_session)
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )
        await _make_participant(db_session, challenge, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/challenges/{challenge.id}/participants?limit=1",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["user_id"] == owner.id
        assert "hashed_password" not in response.text

    @pytest.mark.asyncio
    async def test_admin_can_mark_participant_complete(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.update",)
        )
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )
        participant = await _make_participant(db_session, challenge, owner.id)

        response = await client.post(
            f"/organizations/{org.id}/challenges/{challenge.id}/participants/{owner.id}/complete",
            headers=headers,
            json={"rank": 1},
        )

        assert response.status_code == 200
        assert response.json()["rank"] == 1
        await db_session.refresh(participant)
        assert participant.completed_at is not None


class TestChallengeProgressRoutes:
    @pytest.mark.asyncio
    async def test_submit_progress_persists_entry_and_returns_progress(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        challenge = await _make_challenge(
            db_session, org.id, branch_id=member.branch_id, target_value=100, metric_type="minutes"
        )
        await _make_participant(db_session, challenge, member.id)

        submitted = await client.post(
            f"/organizations/{org.id}/challenges/{challenge.id}/progress",
            headers=headers,
            json={"value": 25, "source": "mobile"},
        )

        assert submitted.status_code == 201
        assert Decimal(submitted.json()["value"]) == Decimal("25")
        entry = (
            await db_session.execute(
                select(ChallengeProgressEntry).where(
                    ChallengeProgressEntry.challenge_id == challenge.id,
                    ChallengeProgressEntry.user_id == member.id,
                )
            )
        ).scalar_one()
        assert float(entry.value) == 25

        progress = await client.get(
            f"/organizations/{org.id}/challenges/{challenge.id}/progress/me",
            headers=headers,
        )
        assert progress.status_code == 200
        assert Decimal(progress.json()["current_value"]) == Decimal("25")
        assert Decimal(progress.json()["percent_complete"]) == Decimal("25")
        assert progress.json()["days_logged"] == 1

    @pytest.mark.asyncio
    async def test_submit_progress_requires_participation(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authorized_org(db_session, role_name="member")
        challenge = await _make_challenge(
            db_session, org.id, branch_id=member.branch_id, metric_type="minutes"
        )

        response = await client.post(
            f"/organizations/{org.id}/challenges/{challenge.id}/progress",
            headers=headers,
            json={"value": 25},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_view_specific_participant_progress(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authorized_org(
            db_session, permissions=("challenge.update",)
        )
        challenge = await _make_challenge(
            db_session, org.id, branch_id=owner.branch_id
        )
        await _make_participant(db_session, challenge, owner.id)

        response = await client.get(
            f"/organizations/{org.id}/challenges/{challenge.id}/progress/{owner.id}",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["user_id"] == owner.id
        assert Decimal(response.json()["current_value"]) == Decimal("0")
