"""
HTTP route tests for app/routers/branch_activities.py.

The route is exercised through the real authentication and database layers.
Tests cover branch visibility, organization isolation, aggregate response
safety, period validation, trend calculation, and missing-resource handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, UserActivityFeed
from tests.conftest import authenticate_user, make_org_with_owner, make_user


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _authenticated_org(
    db_session: AsyncSession, *, role_name: str = "super_admin"
):
    org, user = await make_org_with_owner(
        db_session,
        user_kwargs={"role_name": role_name},
    )
    headers, _ = await authenticate_user(
        db_session,
        user,
        device_fingerprint=f"branch-activities-{user.id}",
    )
    return org, user, headers


async def _add_branch(
    db_session: AsyncSession, organization_id: str, *, name: str
) -> Branch:
    branch = Branch(organization_id=organization_id, name=name)
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _add_activity(
    db_session: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
    activity_type: str,
    created_at: datetime | None = None,
) -> UserActivityFeed:
    activity = UserActivityFeed(
        user_id=user_id,
        organization_id=organization_id,
        activity_type=activity_type,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(activity)
    await db_session.flush()
    return activity


# ── Access control ───────────────────────────────────────────────────────────

class TestBranchActivitySummaryAccess:

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/"
            "branches/00000000-0000-0000-0000-000000000001/activities/summary"
        )

        assert response.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_regular_member_can_view_own_branch(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authenticated_org(
            db_session, role_name="member"
        )

        response = await client.get(
            f"/organizations/{org.id}/branches/{member.branch_id}/activities/summary",
            headers=headers,
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_regular_member_cannot_view_another_branch(
        self, client, db_session: AsyncSession
    ):
        org, member, headers = await _authenticated_org(
            db_session, role_name="member"
        )
        other_branch = await _add_branch(db_session, org.id, name="Abuja")

        response = await client.get(
            f"/organizations/{org.id}/branches/{other_branch.id}/activities/summary",
            headers=headers,
        )

        assert response.status_code == 403
        assert "branch" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_super_admin_can_view_another_branch(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authenticated_org(db_session)
        other_branch = await _add_branch(db_session, org.id, name="Abuja")

        response = await client.get(
            f"/organizations/{org.id}/branches/{other_branch.id}/activities/summary",
            headers=headers,
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_a_different_organization(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authenticated_org(db_session)
        other_org, other_owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "activity-other-org", "business_name": "Activity Other"},
            user_kwargs={"email": "activity-other@example.com"},
        )

        response = await client.get(
            f"/organizations/{other_org.id}/branches/{other_owner.branch_id}/activities/summary",
            headers=headers,
        )

        assert response.status_code == 403


# ── Aggregation and response safety ──────────────────────────────────────────

class TestBranchActivitySummaryResponse:

    @pytest.mark.asyncio
    async def test_returns_branch_only_counts_grouped_by_type(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authenticated_org(db_session)
        other_branch = await _add_branch(db_session, org.id, name="Other")
        other_member = make_user(
            org.id,
            email="activity-other-member@example.com",
            branch_id=other_branch.id,
            status="active",
        )
        db_session.add(other_member)
        await db_session.flush()
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="created_post",
        )
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="created_post",
        )
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="joined_event",
        )
        await _add_activity(
            db_session,
            user_id=other_member.id,
            organization_id=org.id,
            activity_type="created_post",
        )

        response = await client.get(
            f"/organizations/{org.id}/branches/{owner.branch_id}/activities/summary",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total_count"] == 3
        assert {row["activity_type"]: row["count"] for row in body["by_type"]} == {
            "created_post": 2,
            "joined_event": 1,
        }
        assert body["trend"] is None
        assert "items" not in body
        assert "user_id" not in response.text

    @pytest.mark.asyncio
    async def test_period_returns_current_and_previous_trend(
        self, client, db_session: AsyncSession
    ):
        org, owner, headers = await _authenticated_org(db_session)
        now = datetime.now(timezone.utc)
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="created_post",
            created_at=now - timedelta(days=2),
        )
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="created_post",
            created_at=now - timedelta(days=3),
        )
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="created_post",
            created_at=now - timedelta(days=10),
        )

        response = await client.get(
            f"/organizations/{org.id}/branches/{owner.branch_id}/activities/summary?period=week",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["trend"] == {
            "current": 2,
            "previous": 1,
            "change_pct": 100.0,
        }

    @pytest.mark.asyncio
    async def test_invalid_period_is_rejected(self, client, db_session: AsyncSession):
        org, owner, headers = await _authenticated_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/branches/{owner.branch_id}/activities/summary?period=quarter",
            headers=headers,
        )

        assert response.status_code == 422


# ── Missing resource ─────────────────────────────────────────────────────────

class TestBranchActivitySummaryNotFound:

    @pytest.mark.asyncio
    async def test_unknown_branch_returns_not_found(
        self, client, db_session: AsyncSession
    ):
        org, _, headers = await _authenticated_org(db_session)

        response = await client.get(
            f"/organizations/{org.id}/branches/00000000-0000-0000-0000-000000000000/activities/summary",
            headers=headers,
        )

        assert response.status_code == 404
        assert "branch" in response.json()["detail"].lower()
