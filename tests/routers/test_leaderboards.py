"""HTTP route tests for app/routers/leaderboards.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession, *, role_name="member"):
    org, user = await make_org_with_owner(db_session, user_kwargs={"role_name": role_name})
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"leaderboard-router-{user.id}")
    return org, user, headers


class TestLeaderboardRoutes:
    @pytest.mark.asyncio
    async def test_branch_and_org_return_empty_rankings_before_snapshot_job(self, client, db_session):
        org, user, headers = await _context(db_session)

        branch = await client.get(
            f"/organizations/{org.id}/leaderboard/branch?metric_type=STEPS", headers=headers
        )
        assert branch.status_code == 200
        assert branch.json()["scope"] == "branch"
        assert branch.json()["metric_type"] == "steps"
        assert branch.json()["items"] == []

        organization = await client.get(
            f"/organizations/{org.id}/leaderboard/org?metric_type=steps&period_type=weekly", headers=headers
        )
        assert organization.status_code == 200
        assert organization.json()["scope"] == "org"
        assert organization.json()["items"] == []

    @pytest.mark.asyncio
    async def test_member_cannot_view_another_branch(self, client, db_session):
        org, _, headers = await _context(db_session)
        other = Branch(organization_id=org.id, name="Other")
        db_session.add(other)
        await db_session.flush()
        response = await client.get(
            f"/organizations/{org.id}/leaderboard/branch?metric_type=steps&branch_id={other.id}", headers=headers
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_metric_type_is_required(self, client, db_session):
        org, _, headers = await _context(db_session)
        response = await client.get(f"/organizations/{org.id}/leaderboard/org", headers=headers)
        assert response.status_code == 422
