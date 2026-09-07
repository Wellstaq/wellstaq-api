"""HTTP route tests for app/routers/department_ranks.py."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)
    headers, _ = await authenticate_user(db_session, user, device_fingerprint=f"rank-router-{user.id}")
    department = Department(
        organization_id=org.id, branch_id=user.branch_id, name="Engineering", created_by=user.id
    )
    db_session.add(department)
    await db_session.flush()
    return org, department, headers


class TestDepartmentRankRoutes:
    @pytest.mark.asyncio
    async def test_all_rank_routes_return_empty_or_null_before_snapshot_job(self, client, db_session):
        org, department, headers = await _context(db_session)

        listing = await client.get(f"/organizations/{org.id}/department-rank", headers=headers)
        assert listing.status_code == 200
        assert listing.json()["items"] == []

        latest = await client.get(
            f"/organizations/{org.id}/departments/{department.id}/rank", headers=headers
        )
        assert latest.status_code == 200
        assert latest.json()["department_id"] == department.id
        assert latest.json()["rank"] is None

        history = await client.get(
            f"/organizations/{org.id}/departments/{department.id}/rank-history", headers=headers
        )
        assert history.status_code == 200
        assert history.json()["items"] == []

    @pytest.mark.asyncio
    async def test_unknown_department_returns_not_found(self, client, db_session):
        org, _, headers = await _context(db_session)
        response = await client.get(
            f"/organizations/{org.id}/departments/missing/rank", headers=headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client):
        response = await client.get(
            "/organizations/00000000-0000-0000-0000-000000000000/department-rank"
        )
        assert response.status_code in {401, 403}
