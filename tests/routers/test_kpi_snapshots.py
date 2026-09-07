"""HTTP route tests for app/routers/kpi_snapshots.py."""

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession, *, permissions=()):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(
        db_session, user, permissions=permissions, device_fingerprint=f"kpi-router-{user.id}"
    )
    return org, user, headers


class TestKPISnapshotRoutes:
    @pytest.mark.asyncio
    async def test_empty_branch_and_org_wide_snapshots(self, client, db_session):
        org, user, headers = await _context(db_session)
        start = date.today() - timedelta(days=7)
        end = date.today()
        branch = await client.get(
            f"/organizations/{org.id}/branches/{user.branch_id}/kpi-snapshots"
            f"?metric_name=steps&period_start={start}&period_end={end}", headers=headers
        )
        assert branch.status_code == 200
        assert branch.json()["items"] == []

        org_wide = await client.get(
            f"/organizations/{org.id}/kpi-snapshots/org-wide"
            f"?metric_name=STEPS&period_start={start}&period_end={end}", headers=headers
        )
        assert org_wide.status_code == 200
        assert org_wide.json()["metric_name"] == "steps"
        assert org_wide.json()["points"] == []

    @pytest.mark.asyncio
    async def test_overview_requires_wellbeing_permission(self, client, db_session):
        org, _, headers = await _context(db_session, permissions=("wellbeing.view_team",))
        response = await client.get(f"/organizations/{org.id}/kpi-snapshots/overview", headers=headers)
        assert response.status_code == 200
        assert response.json()["period"] == "nine_months"

    @pytest.mark.asyncio
    async def test_snapshot_query_requires_dates_and_metric(self, client, db_session):
        org, user, headers = await _context(db_session)
        response = await client.get(
            f"/organizations/{org.id}/branches/{user.branch_id}/kpi-snapshots", headers=headers
        )
        assert response.status_code == 422
