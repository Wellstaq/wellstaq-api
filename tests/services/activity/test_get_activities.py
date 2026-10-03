"""
Tests for app/services/activity/get_activities.py.

The tests use persisted organizations, branches, users, and activity rows.
They cover user feeds, organization/branch aggregation, pagination, tenant
isolation, branch validation, and current-versus-previous trend windows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, UserActivityFeed
from app.services.activity.get_activities import (
    get_activity_trend,
    get_branch_activity_summary,
    get_organization_activity_summary,
    get_user_activities,
    get_user_activity_count,
)
from tests.conftest import make_org_with_owner, make_user


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _add_branch(
    db_session: AsyncSession, organization_id: str, name: str
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
    activity_type: str = "created_post",
    created_at: datetime | None = None,
) -> UserActivityFeed:
    row = UserActivityFeed(
        user_id=user_id,
        organization_id=organization_id,
        activity_type=activity_type,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(row)
    await db_session.flush()
    return row


# ── User activity feed ───────────────────────────────────────────────────────

class TestGetUserActivities:

    @pytest.mark.asyncio
    async def test_returns_newest_first_with_total_and_pagination(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        now = datetime.now(timezone.utc)
        older = await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            created_at=now - timedelta(hours=2),
        )
        newer = await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="joined_event",
            created_at=now - timedelta(hours=1),
        )

        rows, total = await get_user_activities(
            session=db_session,
            organization_id=org.id,
            user_id=owner.id,
            offset=0,
            limit=1,
        )

        assert total == 2
        assert [row.id for row in rows] == [newer.id]
        assert older.id not in [row.id for row in rows]

    @pytest.mark.asyncio
    async def test_feed_and_count_are_scoped_to_user_and_organization(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        other = make_user(
            org.id, email="activity-count-other@example.com", branch_id=owner.branch_id
        )
        db_session.add(other)
        await db_session.flush()
        await _add_activity(db_session, user_id=owner.id, organization_id=org.id)
        await _add_activity(db_session, user_id=other.id, organization_id=org.id)

        rows, total = await get_user_activities(
            session=db_session, organization_id=org.id, user_id=owner.id
        )
        count = await get_user_activity_count(
            session=db_session, organization_id=org.id, user_id=owner.id
        )

        assert total == count == 1
        assert {row.user_id for row in rows} == {owner.id}


# ── Organization aggregate ───────────────────────────────────────────────────

class TestOrganizationActivitySummary:

    @pytest.mark.asyncio
    async def test_groups_counts_and_excludes_other_organizations(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        other_org, other_owner = await make_org_with_owner(
            db_session,
            with_trial=False,
            org_kwargs={"slug": "activity-summary-other", "business_name": "Other"},
            user_kwargs={"email": "activity-summary-other@example.com"},
        )
        await _add_activity(db_session, user_id=owner.id, organization_id=org.id)
        await _add_activity(db_session, user_id=owner.id, organization_id=org.id)
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            activity_type="joined_event",
        )
        await _add_activity(
            db_session,
            user_id=other_owner.id,
            organization_id=other_org.id,
            activity_type="created_post",
        )

        total, by_type = await get_organization_activity_summary(
            session=db_session, organization_id=org.id
        )

        assert total == 3
        assert dict(by_type) == {"created_post": 2, "joined_event": 1}


# ── Branch aggregate ─────────────────────────────────────────────────────────

class TestBranchActivitySummary:

    @pytest.mark.asyncio
    async def test_counts_only_users_whose_home_branch_matches(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        other_branch = await _add_branch(db_session, org.id, "Other Branch")
        other_user = make_user(
            org.id,
            email="other-branch-activity@example.com",
            branch_id=other_branch.id,
        )
        db_session.add(other_user)
        await db_session.flush()
        await _add_activity(db_session, user_id=owner.id, organization_id=org.id)
        await _add_activity(db_session, user_id=other_user.id, organization_id=org.id)

        total, by_type = await get_branch_activity_summary(
            session=db_session,
            organization_id=org.id,
            branch_id=owner.branch_id,
        )

        assert total == 1
        assert by_type == [("created_post", 1)]

    @pytest.mark.asyncio
    async def test_unknown_branch_returns_404(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session, with_trial=False)

        with pytest.raises(HTTPException) as exc:
            await get_branch_activity_summary(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc.value.status_code == 404
        assert "branch" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_branch_from_another_org_returns_404(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session, with_trial=False)
        other_org, other_owner = await make_org_with_owner(
            db_session,
            with_trial=False,
            org_kwargs={"slug": "activity-foreign-branch", "business_name": "Foreign"},
            user_kwargs={"email": "activity-foreign-branch@example.com"},
        )

        with pytest.raises(HTTPException) as exc:
            await get_branch_activity_summary(
                session=db_session,
                organization_id=org.id,
                branch_id=other_owner.branch_id,
            )

        assert exc.value.status_code == 404


# ── Trends ───────────────────────────────────────────────────────────────────

class TestActivityTrend:

    @pytest.mark.asyncio
    async def test_no_period_returns_no_trend(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session, with_trial=False)

        result = await get_activity_trend(
            session=db_session, organization_id=org.id
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_week_compares_current_and_previous_window(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        now = datetime.now(timezone.utc)
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            created_at=now - timedelta(days=2),
        )
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            created_at=now - timedelta(days=3),
        )
        await _add_activity(
            db_session,
            user_id=owner.id,
            organization_id=org.id,
            created_at=now - timedelta(days=10),
        )

        result = await get_activity_trend(
            session=db_session,
            organization_id=org.id,
            period="week",
        )

        assert result == {"current": 2, "previous": 1, "change_pct": 100.0}

    @pytest.mark.asyncio
    async def test_zero_previous_count_returns_unknown_percentage(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session, with_trial=False)
        await _add_activity(db_session, user_id=owner.id, organization_id=org.id)

        result = await get_activity_trend(
            session=db_session,
            organization_id=org.id,
            period="custom",
            start_date=date.today() - timedelta(days=1),
            end_date=date.today(),
        )

        assert result["current"] == 1
        assert result["previous"] == 0
        assert result["change_pct"] is None
