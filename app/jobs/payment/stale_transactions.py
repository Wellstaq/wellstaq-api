"""
app/jobs/payment/stale_transactions.py

expire_stale_pending_transactions — flips PaymentTransaction rows stuck
in 'pending' for too long (abandoned checkouts — customer closed the
tab, never completed payment) to 'cancelled'. Without this, the
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
from app.core.config import settings

logger = logging.getLogger(__name__)

async def expire_stale_pending_transactions(ctx: dict[str, Any]) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.PAYMENT_PENDING_EXPIRY_MINUTES)
    expired = 0

    async with get_job_session() as session:
        result = await session.execute(
            select(PaymentTransaction).where(
                PaymentTransaction.status == "pending",
                PaymentTransaction.created_at <= cutoff,
            )
        )
        for transaction in result.scalars().all():
            transaction.status = "cancelled"
            transaction.failure_reason = f"Checkout expired — no payment confirmation received within {settings.PAYMENT_PENDING_EXPIRY_MINUTES} minutes."
            expired += 1

        await session.commit()

    logger.info("Stale pending transactions expired", extra={"expired_count": expired})
