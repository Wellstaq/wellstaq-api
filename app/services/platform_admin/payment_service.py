"""
app/services/platform_admin/payment_service.py

Admin-scoped READ access to payment transactions — lets platform staff
look up a transaction by its gateway reference or internal id when an
organization reports a billing problem, and view an organization's full
transaction history. Read-only: refunds/corrections happen through the
payment gateway directly, not here.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import PaymentTransaction
from app.services.platform_admin.organization_service import get_organization_or_404


def _serialize(transaction: PaymentTransaction, *, include_org: bool = False) -> dict:
    row = {
        "id": transaction.id, "amount": transaction.amount, "currency": transaction.currency,
        "status": transaction.status, "payment_method": transaction.payment_method,
        "is_recurring": transaction.is_recurring, "external_reference": transaction.external_reference,
        "failure_reason": transaction.failure_reason, "created_at": transaction.created_at,
    }
    if include_org:
        row["organization_id"] = transaction.organization_id
        row["organization_name"] = transaction.organization.business_name if transaction.organization else None
    return row


async def list_org_transactions(*, session: AsyncSession, org_id: str, offset: int, limit: int) -> tuple[list[dict], int]:
    await get_organization_or_404(session=session, org_id=org_id)
    query = select(PaymentTransaction).where(PaymentTransaction.organization_id == org_id)
    count_query = select(func.count()).select_from(PaymentTransaction).where(PaymentTransaction.organization_id == org_id)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(PaymentTransaction.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return [_serialize(t) for t in rows], total


async def search_transactions(*, session: AsyncSession, q: str, offset: int, limit: int) -> tuple[list[dict], int]:
    """Cross-organization lookup by gateway reference or internal
    transaction id — for support/billing investigations where an admin
    only has the invoice/payment reference an organization sent them,
    not which organization it belongs to."""
    pattern = f"%{q}%"
    clause = or_(PaymentTransaction.external_reference.ilike(pattern), PaymentTransaction.id.ilike(pattern))
    query = select(PaymentTransaction).options(selectinload(PaymentTransaction.organization)).where(clause)
    count_query = select(func.count()).select_from(PaymentTransaction).where(clause)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(PaymentTransaction.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return [_serialize(t, include_org=True) for t in rows], total
