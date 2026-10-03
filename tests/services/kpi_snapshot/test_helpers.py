"""Tests for KPI snapshot branch validation."""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.kpi_snapshot._helpers import validate_branch
from tests.conftest import make_org_with_owner


@pytest.mark.asyncio
async def test_validate_branch_accepts_branch_in_organization(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    assert await validate_branch(
        session=db_session, organization_id=org.id, branch_id=owner.branch_id
    ) is None


@pytest.mark.asyncio
async def test_validate_branch_rejects_cross_tenant_branch(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)

    with pytest.raises(HTTPException) as exc:
        await validate_branch(
            session=db_session,
            organization_id="00000000-0000-0000-0000-000000000000",
            branch_id=owner.branch_id,
        )
    assert exc.value.status_code == 404
