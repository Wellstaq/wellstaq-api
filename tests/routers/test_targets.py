"""HTTP route tests for app/routers/targets.py."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Target
from tests.conftest import authenticate_user, make_org_with_owner


async def _context(db_session: AsyncSession, *, role_name="super_admin", permissions=("branch.update",)):
    org, user = await make_org_with_owner(db_session, user_kwargs={"role_name": role_name})
    headers, _ = await authenticate_user(
        db_session, user, permissions=permissions, device_fingerprint=f"target-router-{user.id}"
    )
    return org, user, headers


class TestTargetRoutes:
    @pytest.mark.asyncio
    async def test_set_list_progress_and_delete_target(self, client, db_session):
        org, user, headers = await _context(db_session)
        base = f"/organizations/{org.id}/branches/{user.branch_id}/targets"
        payload = {"metric_type": "steps", "period_type": "monthly", "period": "2026-09", "target_value": 10000}

        created = await client.put(base, headers=headers, json=payload)
        assert created.status_code == 200
        target_id = created.json()["id"]
        assert Decimal(created.json()["target_value"]) == Decimal("10000")

        listed = await client.get(base, headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1

        progress = await client.get(
            f"{base}/progress?metric_type=steps&period_type=monthly&period=2026-09", headers=headers
        )
        assert progress.status_code == 200
        assert progress.json()["has_target"] is True

        deleted = await client.delete(
            f"/organizations/{org.id}/targets/{target_id}", headers=headers
        )
        assert deleted.status_code == 204
        assert (await db_session.execute(select(Target).where(Target.id == target_id))).scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_rejects_non_positive_target(self, client, db_session):
        org, user, headers = await _context(db_session)
        response = await client.put(
            f"/organizations/{org.id}/branches/{user.branch_id}/targets",
            headers=headers,
            json={"metric_type": "steps", "period_type": "daily", "period": "2026-09-07", "target_value": 0},
        )
        assert response.status_code == 422
