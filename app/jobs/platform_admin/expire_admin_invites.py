"""
app/jobs/platform_admin/expire_admin_invites.py

Same convention as the org app's employee invites (INVITE_TTL_DAYS,
app/services/organization/invite_service.py): an admin invite that sits
unanswered for that many days is swept into the same disabled state as
a manually removed admin (see admin_team_service.remove_admin). This is
what makes "I lost the invite link" self-resolving — invite_admin
already resurrects a disabled row on re-invite (see its docstring), so
once an invite expires, sending a new one just works without anyone
having to notice and manually remove the stale row first.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.constants import INVITE_TTL_DAYS
from app.jobs.shared import get_job_session
from app.models import AdminUser

logger = logging.getLogger(__name__)


async def expire_admin_invites(ctx: dict[str, Any]) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=INVITE_TTL_DAYS)
    expired = 0

    async with get_job_session() as session:
        result = await session.execute(
            select(AdminUser).where(
                AdminUser.status == "invited",
                AdminUser.invited_at.is_not(None),
                AdminUser.invited_at <= cutoff,
            )
        )
        for admin in result.scalars().all():
            admin.status = "disabled"
            expired += 1
        await session.commit()

    logger.info("Admin invites expired", extra={"expired": expired})
