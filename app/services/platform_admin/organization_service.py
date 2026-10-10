"""
app/services/platform_admin/organization_service.py

Admin-scoped READ and lifecycle (suspend/reactivate) access to
organizations. Projects existing Organization/User/Branch/Department/
Club/Event rows into the admin console's shapes — never returns
wellbeing, pulse, check-in, chat, or participant-identity data (see
docs/ADMIN_API_CONTRACT.md section 2 on the admin console repo).

Suspend pauses billing: disables Paystack auto-renew if it was on
(tracked so reactivate can restore it) and blocks every member's
access immediately via Organization.is_active, enforced in
app/auth/dependencies.py and login_service.py. No data is touched or
deleted — see Organization model's own docstring on is_active.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Branch,
    Club,
    Department,
    DepartmentMember,
    Event,
    EventParticipant,
    Organization,
    OrganizationSubscription,
    User,
)
from app.services.payment.cancel_service import cancel_auto_renew
from app.services.payment.paystack_client import PaystackError, enable_subscription


async def get_organization_or_404(*, session: AsyncSession, org_id: str) -> Organization:
    result = await session.execute(
        select(Organization).options(
            selectinload(Organization.subscription).selectinload(OrganizationSubscription.plan),
            selectinload(Organization.owner),
        ).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")
    return org


def _status(org: Organization) -> str:
    if not org.is_active:
        return "suspended"
    if org.owner_id is None:
        return "pending_setup"
    return "active"


def _summary(org: Organization, employee_count: int, branch_count: int) -> dict:
    sub = org.subscription
    return {
        "id": org.id,
        "name": org.business_name,
        "logo_url": None,
        "industry": org.organization_type,
        "country": None,
        "status": _status(org),
        "work_model": org.work_model,
        "employee_count": employee_count,
        "branch_count": branch_count,
        "plan_name": sub.plan.name if sub and sub.plan else None,
        "subscription_status": sub.status if sub else None,
        "platform_comped": bool(sub and sub.platform_comped),
        "owner": {"name": f"{org.owner.first_name} {org.owner.last_name}".strip(), "email": org.owner.email} if org.owner else None,
        "created_at": org.created_at,
    }


async def list_organizations(
    *, session: AsyncSession, q: str | None, status_filter: str | None,
    sort: str | None, offset: int, limit: int,
) -> tuple[list[dict], int]:
    query = select(Organization).options(selectinload(Organization.subscription).selectinload(OrganizationSubscription.plan), selectinload(Organization.owner))
    count_query = select(func.count()).select_from(Organization)

    if q:
        pattern = f"%{q}%"
        query = query.join(User, User.id == Organization.owner_id, isouter=True).where(
            or_(Organization.business_name.ilike(pattern), User.email.ilike(pattern))
        )
        count_query = count_query.join(User, User.id == Organization.owner_id, isouter=True).where(
            or_(Organization.business_name.ilike(pattern), User.email.ilike(pattern))
        )

    if status_filter == "active":
        query = query.where(Organization.is_active.is_(True), Organization.owner_id.is_not(None))
        count_query = count_query.where(Organization.is_active.is_(True), Organization.owner_id.is_not(None))
    elif status_filter == "suspended":
        query = query.where(Organization.is_active.is_(False))
        count_query = count_query.where(Organization.is_active.is_(False))
    elif status_filter == "pending_setup":
        query = query.where(Organization.owner_id.is_(None))
        count_query = count_query.where(Organization.owner_id.is_(None))

    sort_map = {"name": Organization.business_name.asc(), "-created_at": Organization.created_at.desc(), "created_at": Organization.created_at.asc()}
    query = query.order_by(sort_map.get(sort or "-created_at", Organization.created_at.desc())).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    orgs = (await session.execute(query)).scalars().all()

    items = []
    for org in orgs:
        employee_count = (await session.execute(
            select(func.count()).select_from(User).where(User.organization_id == org.id)
        )).scalar_one()
        branch_count = (await session.execute(
            select(func.count()).select_from(Branch).where(Branch.organization_id == org.id)
        )).scalar_one()
        items.append(_summary(org, employee_count, branch_count))
    return items, total


async def get_organization_detail(*, session: AsyncSession, org_id: str) -> dict:
    org = await get_organization_or_404(session=session, org_id=org_id)
    employee_count = (await session.execute(select(func.count()).select_from(User).where(User.organization_id == org_id))).scalar_one()
    branch_count = (await session.execute(select(func.count()).select_from(Branch).where(Branch.organization_id == org_id))).scalar_one()
    department_count = (await session.execute(select(func.count()).select_from(Department).where(Department.organization_id == org_id))).scalar_one()
    club_count = (await session.execute(select(func.count()).select_from(Club).where(Club.organization_id == org_id))).scalar_one()
    event_count = (await session.execute(select(func.count()).select_from(Event).where(Event.organization_id == org_id))).scalar_one()

    summary = _summary(org, employee_count, branch_count)
    return {
        **summary,
        "business_email": None,
        "phone_number": None,
        "website": org.business_website,
        "address": None,
        "city": None,
        "state": None,
        "size_range": None,
        "counts": {"branches": branch_count, "departments": department_count, "clubs": club_count, "events": event_count, "employees": employee_count},
        "suspended_at": org.deactivated_at,
        "suspension_reason": org.deactivation_reason,
    }


async def suspend_organization(*, session: AsyncSession, org_id: str, reason: str) -> dict:
    org = await get_organization_or_404(session=session, org_id=org_id)
    if not org.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization is already suspended.")

    # Pause billing: disable Paystack auto-renew so the org isn't charged
    # while suspended. cancel_auto_renew() is a no-op-safe call elsewhere
    # but here we track whether it WAS on, to restore on reactivate —
    # it has no "was this already off" signal of its own.
    sub = org.subscription
    was_auto_renewing = bool(sub and sub.auto_renew)
    if was_auto_renewing:
        try:
            await cancel_auto_renew(session=session, organization_id=org_id)
        except HTTPException:
            # Paystack-side failure shouldn't block the suspension itself —
            # the org is still locked out via is_active either way.
            pass
        # Remember intent to resume billing on reactivate, independent of
        # whatever Paystack's own auto_renow flag now reads.
        sub.auto_renew = was_auto_renewing

    org.is_active = False
    org.deactivated_at = datetime.now(timezone.utc)
    org.deactivation_reason = reason
    await session.flush()
    return await get_organization_detail(session=session, org_id=org_id)


async def reactivate_organization(*, session: AsyncSession, org_id: str) -> dict:
    org = await get_organization_or_404(session=session, org_id=org_id)
    if org.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization is not suspended.")

    org.is_active = True
    org.deactivated_at = None
    org.deactivation_reason = None

    sub = org.subscription
    if sub and sub.auto_renew and sub.external_subscription_id and sub.email_token:
        try:
            await enable_subscription(subscription_code=sub.external_subscription_id, email_token=sub.email_token)
        except PaystackError:
            # Billing resume failing shouldn't block reactivation — the
            # org owner can retry from their own billing settings.
            pass

    await session.flush()
    return await get_organization_detail(session=session, org_id=org_id)


async def grant_free_access(*, session: AsyncSession, org_id: str) -> dict:
    """Admin comp — lets an org use the app with no paid subscription.
    Independent of suspend/reactivate: a comped org can still be
    suspended (is_active still gates access), and suspending does not
    clear the comp — the two are orthogonal admin levers."""
    org = await get_organization_or_404(session=session, org_id=org_id)
    if org.subscription is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization has no subscription record to comp.")
    if org.subscription.platform_comped:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization is already on free access.")
    org.subscription.platform_comped = True
    await session.flush()
    return await get_organization_detail(session=session, org_id=org_id)


async def revoke_free_access(*, session: AsyncSession, org_id: str) -> dict:
    org = await get_organization_or_404(session=session, org_id=org_id)
    if org.subscription is None or not org.subscription.platform_comped:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organization is not on free access.")
    org.subscription.platform_comped = False
    await session.flush()
    return await get_organization_detail(session=session, org_id=org_id)


async def list_members(*, session: AsyncSession, org_id: str, q: str | None, status_filter: str | None, offset: int, limit: int) -> tuple[list[dict], int]:
    await get_organization_or_404(session=session, org_id=org_id)
    query = select(User).options(
        selectinload(User.branch),
        selectinload(User.department_memberships).selectinload(DepartmentMember.department),
        selectinload(User.role),
    ).where(User.organization_id == org_id)
    count_query = select(func.count()).select_from(User).where(User.organization_id == org_id)
    if q:
        pattern = f"%{q}%"
        clause = or_(User.first_name.ilike(pattern), User.last_name.ilike(pattern), User.email.ilike(pattern))
        query, count_query = query.where(clause), count_query.where(clause)
    if status_filter:
        query, count_query = query.where(User.status == status_filter), count_query.where(User.status == status_filter)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(User.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    items = [{
        "id": u.id, "first_name": u.first_name, "last_name": u.last_name, "email": u.email,
        "avatar_url": u.avatar_url, "branch_name": u.branch.name if u.branch else None,
        "department_name": u.department_memberships[0].department.name if u.department_memberships else None,
        "role": u.role.name if u.role else None, "status": u.status, "joined_at": u.created_at,
    } for u in rows]
    return items, total


async def list_branches(*, session: AsyncSession, org_id: str, q: str | None, offset: int, limit: int) -> tuple[list[dict], int]:
    await get_organization_or_404(session=session, org_id=org_id)
    query = select(Branch).options(selectinload(Branch.manager)).where(Branch.organization_id == org_id)
    count_query = select(func.count()).select_from(Branch).where(Branch.organization_id == org_id)
    if q:
        query, count_query = query.where(Branch.name.ilike(f"%{q}%")), count_query.where(Branch.name.ilike(f"%{q}%"))
    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(Branch.name.asc()).offset(offset).limit(limit))).scalars().all()
    items = []
    for b in rows:
        member_count = (await session.execute(select(func.count()).select_from(User).where(User.branch_id == b.id))).scalar_one()
        items.append({
            "id": b.id, "name": b.name, "city": None, "country": None,
            "member_count": member_count,
            "manager_name": f"{b.manager.first_name} {b.manager.last_name}".strip() if b.manager else None,
        })
    return items, total


async def list_departments(*, session: AsyncSession, org_id: str, q: str | None, offset: int, limit: int) -> tuple[list[dict], int]:
    await get_organization_or_404(session=session, org_id=org_id)
    query = select(Department).options(selectinload(Department.branch), selectinload(Department.head)).where(Department.organization_id == org_id)
    count_query = select(func.count()).select_from(Department).where(Department.organization_id == org_id)
    if q:
        query, count_query = query.where(Department.name.ilike(f"%{q}%")), count_query.where(Department.name.ilike(f"%{q}%"))
    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(Department.name.asc()).offset(offset).limit(limit))).scalars().all()
    items = [{
        "id": d.id, "name": d.name, "branch_name": d.branch.name if d.branch else None,
        "member_count": d.member_count, "head_name": f"{d.head.first_name} {d.head.last_name}".strip() if d.head else None,
    } for d in rows]
    return items, total


async def list_clubs(*, session: AsyncSession, org_id: str, q: str | None, offset: int, limit: int) -> tuple[list[dict], int]:
    await get_organization_or_404(session=session, org_id=org_id)
    query = select(Club).options(selectinload(Club.branch)).where(Club.organization_id == org_id)
    count_query = select(func.count()).select_from(Club).where(Club.organization_id == org_id)
    if q:
        query, count_query = query.where(Club.name.ilike(f"%{q}%")), count_query.where(Club.name.ilike(f"%{q}%"))
    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(Club.name.asc()).offset(offset).limit(limit))).scalars().all()
    items = [{
        "id": c.id, "name": c.name, "description": c.description, "image_url": c.image_url,
        "branch_name": c.branch.name if c.branch else None, "member_count": c.member_count, "created_at": c.created_at,
    } for c in rows]
    return items, total


async def list_org_events(*, session: AsyncSession, org_id: str, q: str | None, status_filter: str | None, sort: str | None, offset: int, limit: int) -> tuple[list[dict], int]:
    await get_organization_or_404(session=session, org_id=org_id)
    query = select(Event).options(selectinload(Event.branch)).where(Event.organization_id == org_id)
    count_query = select(func.count()).select_from(Event).where(Event.organization_id == org_id)
    if q:
        query, count_query = query.where(Event.title.ilike(f"%{q}%")), count_query.where(Event.title.ilike(f"%{q}%"))
    if status_filter:
        query, count_query = query.where(Event.status == status_filter), count_query.where(Event.status == status_filter)
    order = Event.start_date.asc() if sort != "-start_date" else Event.start_date.desc()
    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(order).offset(offset).limit(limit))).scalars().all()
    items = []
    for e in rows:
        participant_count = (await session.execute(select(func.count()).select_from(EventParticipant).where(EventParticipant.event_id == e.id))).scalar_one()
        items.append({
            "id": e.id, "title": e.title, "description": e.description, "image_url": e.image_url,
            "start_date": e.start_date, "end_date": e.end_date, "time": e.time, "status": e.status,
            "branch_name": e.branch.name if e.branch else None, "participant_count": participant_count, "created_at": e.created_at,
        })
    return items, total
