from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationIntegration

CATALOGUE = {
    "google_calendar": ("Google Calendar", "Sync wellness events and challenges to Google Calendar."),
    "slack": ("Slack", "Send wellness challenge and activity notifications to Slack."),
    "zoom": ("Zoom", "Generate Zoom links for virtual wellness sessions."),
    "microsoft_outlook": ("Microsoft Outlook", "Sync events and reminders through Outlook."),
}


async def list_org_integrations(db: AsyncSession, org_id: str) -> list[dict]:
    rows = (await db.execute(
        select(OrganizationIntegration).where(OrganizationIntegration.organization_id == org_id)
    )).scalars().all()
    states = {row.provider: row for row in rows}
    return [
        {
            "id": provider,
            "name": name,
            "description": description,
            "status": "connected" if states.get(provider) and states[provider].connected else "disconnected",
            "enabled": bool(states.get(provider) and states[provider].enabled),
        }
        for provider, (name, description) in CATALOGUE.items()
    ]


def get_catalogue_entry(provider: str) -> tuple[str, str]:
    if provider not in CATALOGUE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration provider not found.")
    return CATALOGUE[provider]


async def toggle_org_integration(db: AsyncSession, org_id: str, provider: str, enabled: bool) -> OrganizationIntegration:
    row = (await db.execute(
        select(OrganizationIntegration).where(
            OrganizationIntegration.organization_id == org_id,
            OrganizationIntegration.provider == provider,
        )
    )).scalar_one_or_none()
    if row is None or not row.connected:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect this integration before enabling it.")
    row.enabled = enabled
    await db.flush()
    return row


async def disconnect_org_integration(db: AsyncSession, org_id: str, provider: str) -> None:
    row = (await db.execute(
        select(OrganizationIntegration).where(
            OrganizationIntegration.organization_id == org_id,
            OrganizationIntegration.provider == provider,
        )
    )).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.flush()