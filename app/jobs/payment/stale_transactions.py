"""
app/jobs/payment/stale_transactions.py

expire_stale_pending_transactions — flips PaymentTransaction rows stuck
in 'pending' for too long (abandoned checkouts — customer closed the
tab, never completed payment) to 'failed'. Without this, the
idempotency guard in checkout_service.initiate_checkout would
permanently block a retry, since it refuses a new checkout while a
pending transaction for the same org+plan already exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.jobs.shared import get_job_session
from app.models.payment import PaymentTransaction

logger = logging.getLogger(__name__)

STALE_PENDING_THRESHOLD = timedelta(hours=1)


async def expire_stale_pending_transactions(ctx: dict[str, Any]) -> None:
    cutoff = datetime.now(timezone.utc) - STALE_PENDING_THRESHOLD
    expired = 0

    async with get_job_session() as session:
        result = await session.execute(
            select(PaymentTransaction).where(
                PaymentTransaction.status == "pending",
                PaymentTransaction.created_at <= cutoff,
            )
        )
        for transaction in result.scalars().all():
            transaction.status = "failed"
            transaction.failure_reason = "Checkout abandoned — no payment confirmation received within 1 hour."
            expired += 1

        await session.commit()

    logger.info("Stale pending transactions expired", extra={"expired_count": expired})