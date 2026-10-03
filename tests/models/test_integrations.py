"""ORM and integrity tests for app/models/integration.py."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    HealthDataConsent,
    HealthMeasurement,
    IntegrationExternalRecord,
    OrganizationIntegration,
    UserIntegration,
)
from tests.conftest import make_org_with_owner


@pytest.mark.asyncio
async def test_organization_integration_persists_defaults(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    integration = OrganizationIntegration(
        organization_id=org.id,
        provider="google_calendar",
        connected_by=owner.id,
    )
    db_session.add(integration)
    await db_session.flush()

    fetched = (await db_session.execute(
        select(OrganizationIntegration).where(OrganizationIntegration.id == integration.id)
    )).scalar_one()
    assert fetched.connected is False
    assert fetched.enabled is False
    assert fetched.settings == {}


@pytest.mark.asyncio
async def test_organization_integration_provider_is_unique_per_org(db_session: AsyncSession):
    org, _ = await make_org_with_owner(db_session, with_trial=False)
    db_session.add_all([
        OrganizationIntegration(organization_id=org.id, provider="google_calendar"),
        OrganizationIntegration(organization_id=org.id, provider="google_calendar"),
    ])

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_integration_persists_provider_identity_and_defaults(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    integration = UserIntegration(
        user_id=owner.id,
        organization_id=org.id,
        provider="google_calendar",
        provider_account_id="google-account-1",
        provider_email=owner.email,
    )
    db_session.add(integration)
    await db_session.flush()

    assert integration.status == "connected"
    assert integration.enabled is True
    assert integration.scopes == []
    assert integration.settings == {}


@pytest.mark.asyncio
async def test_user_can_connect_a_provider_only_once(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    db_session.add_all([
        UserIntegration(user_id=owner.id, organization_id=org.id, provider="fitbit"),
        UserIntegration(user_id=owner.id, organization_id=org.id, provider="fitbit"),
    ])

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_external_record_is_unique_for_local_resource(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    integration = UserIntegration(
        user_id=owner.id,
        organization_id=org.id,
        provider="google_calendar",
    )
    db_session.add(integration)
    await db_session.flush()
    values = dict(
        user_integration_id=integration.id,
        organization_id=org.id,
        local_type="event",
        local_id="11111111-1111-1111-1111-111111111111",
        external_id="google-event-1",
    )
    db_session.add_all([IntegrationExternalRecord(**values), IntegrationExternalRecord(**values)])

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_health_consent_records_grant_and_revocation(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    now = datetime.now(timezone.utc)
    consent = HealthDataConsent(
        user_id=owner.id,
        organization_id=org.id,
        provider="fitbit",
        data_types=["steps", "heart_rate"],
        granted_at=now,
        revoked_at=now,
        consent_version="2026-01",
    )
    db_session.add(consent)
    await db_session.flush()

    assert consent.data_types == ["steps", "heart_rate"]
    assert consent.revoked_at is not None


@pytest.mark.asyncio
async def test_health_measurement_maps_metadata_column_and_optional_integration(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    measurement = HealthMeasurement(
        user_id=owner.id,
        organization_id=org.id,
        user_integration_id=None,
        provider="manual",
        external_id="manual-1",
        metric_type="steps",
        value=7500,
        unit="count",
        measured_at=datetime.now(timezone.utc),
        metadata_json={"source": "mobile"},
    )
    db_session.add(measurement)
    await db_session.flush()

    assert measurement.user_integration_id is None
    assert measurement.metadata_json == {"source": "mobile"}
    assert HealthMeasurement.__table__.c.metadata.name == "metadata"


@pytest.mark.asyncio
async def test_health_measurement_provider_record_is_idempotent(db_session: AsyncSession):
    org, owner = await make_org_with_owner(db_session, with_trial=False)
    values = dict(
        user_id=owner.id,
        organization_id=org.id,
        provider="fitbit",
        external_id="fitbit-record-1",
        metric_type="steps",
        value=5000,
        unit="count",
        measured_at=datetime.now(timezone.utc),
    )
    db_session.add_all([HealthMeasurement(**values), HealthMeasurement(**values)])

    with pytest.raises(IntegrityError):
        await db_session.flush()
