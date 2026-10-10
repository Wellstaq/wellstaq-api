"""
app/services/platform_admin/audit_service.py

One helper, called from every platform-admin write action (organization
suspend/reactivate, user suspend/reactivate/password-reset, platform
event create/update/cancel/delete, admin invite/role-change/remove,
support ticket status changes, admin sign-in success/failure) — see
docs/ADMIN_API_CONTRACT.md section 12 on the admin console repo for the
full list this is expected to cover.

Append-only: never update or delete an AuditEntry row once written.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry


async def log_admin_audit(
    *, session: AsyncSession, actor_admin_id: str | None, action: str,
    target_type: str, target_id: str, target_label: str,
    reason: str | None = None, ip_address: str | None = None, user_agent: str | None = None,
) -> None:
    session.add(AuditEntry(
        actor_admin_id=actor_admin_id, action=action,
        target_type=target_type, target_id=target_id, target_label=target_label,
        reason=reason, ip_address=ip_address, user_agent=user_agent,
    ))
    await session.flush()
