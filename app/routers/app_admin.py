"""
app/routers/app_admin.py

Every /app-admin/* endpoint except support tickets (app/routers/support_tickets.py
already owns that prefix and now also uses get_current_admin — see that
file). One router object registered once in main.py, matching the
contract's "every admin endpoint lives under /app-admin/..." rule.

Permission model: section 3 of docs/ADMIN_API_CONTRACT.md on the admin
console repo — require_admin_permission(...) per route, super_admin
implicitly passes every check (see CurrentAdmin.has_permission).
"""

from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin_dependencies import CurrentAdmin, get_current_admin, require_admin_permission
from app.core.database import get_db
from app.core.guards import request_guard
from app.models import AdminUser
from app.schemas.platform_admin import (
    ActivateSubscriptionRequest,
    AdminForgotPasswordRequest,
    AdminLoginRequest,
    AdminMeResponse,
    AdminRefreshRequest,
    AdminResetPasswordRequest,
    AdminTwoFaVerifyRequest,
    AdminUserOut,
    AuditActorOut,
    AuditEntryOut,
    InviteAdminRequest,
    OrganizationDetailOut,
    PaginatedResponse,
    PlatformEventCreateRequest,
    PlatformEventOut,
    PlatformEventUpdateRequest,
    PlatformOverviewResponse,
    SuspendOrganizationRequest,
    SuspendUserRequest,
    UpdateAdminProfileRequest,
    UpdateAdminRoleRequest,
)
from app.schemas.storage import UploadRequestSchema, UploadResponse
from app.services.platform_admin import admin_team_service, auth_service, organization_service, payment_service, platform_event_service, storage_service
from app.services.platform_admin.audit_service import log_admin_audit

router = APIRouter(prefix="/app-admin", tags=["platform-admin"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    return request.client.host if request.client else None, request.headers.get("user-agent")


# ── Auth ─────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def admin_login_route(body: AdminLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip, _ = _client_meta(request)
    async with request_guard(db, "/app-admin/auth/login"):
        result = await auth_service.login(session=db, email=body.email, password=body.password, ip_address=ip)
        await db.commit()
        return result


@router.post("/auth/2fa/verify")
async def admin_verify_2fa_route(body: AdminTwoFaVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip, _ = _client_meta(request)
    async with request_guard(db, "/app-admin/auth/2fa/verify"):
        result = await auth_service.verify_2fa(session=db, challenge_token=body.challenge_token, code=body.code, ip_address=ip)
        await db.commit()
        return result


@router.post("/auth/refresh")
async def admin_refresh_route(body: AdminRefreshRequest, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/app-admin/auth/refresh"):
        result = await auth_service.refresh(session=db, refresh_token=body.refresh_token)
        await db.commit()
        return result


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def admin_logout_route(db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(get_current_admin)):
    async with request_guard(db, "/app-admin/auth/logout"):
        await auth_service.logout(session=db, session_id=current_admin.session_id)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=AdminMeResponse)
async def admin_me_route(db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(get_current_admin)):
    admin = (await db.execute(select(AdminUser).where(AdminUser.id == current_admin.admin_id))).scalar_one()
    return AdminMeResponse(
        id=admin.id, email=admin.email, first_name=admin.first_name, last_name=admin.last_name,
        role=admin.role, permissions=current_admin.permissions, avatar_url=admin.avatar_url, two_fa_enabled=admin.two_fa_enabled,
    )


@router.patch("/auth/me", response_model=AdminMeResponse)
async def update_admin_profile_route(
    body: UpdateAdminProfileRequest, db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(get_current_admin),
):
    """Self-service only: always the caller's own row (current_admin.admin_id
    from the access token), never a path param — see
    UpdateAdminProfileRequest's docstring for why that's deliberate."""
    async with request_guard(db, "/app-admin/auth/me"):
        admin = (await db.execute(select(AdminUser).where(AdminUser.id == current_admin.admin_id))).scalar_one()
        admin.first_name = body.first_name.strip()
        admin.last_name = body.last_name.strip()
        admin.avatar_url = body.avatar_url
        await db.commit()
        await db.refresh(admin)
        return AdminMeResponse(
            id=admin.id, email=admin.email, first_name=admin.first_name, last_name=admin.last_name,
            role=admin.role, permissions=current_admin.permissions, avatar_url=admin.avatar_url, two_fa_enabled=admin.two_fa_enabled,
        )


@router.post("/storage/upload-url", response_model=UploadResponse)
async def request_admin_upload_url_route(
    body: UploadRequestSchema, db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(get_current_admin),
):
    """Any authenticated admin can request an 'admin-avatars' upload URL
    (self-only — nothing is attributed until PATCH /auth/me stores it on
    their own row). 'platform-events' additionally requires events.manage,
    matching the permission already required to create/edit the event
    that will reference it."""
    if body.domain == "platform-events" and not current_admin.has_permission("events.manage"):
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your admin role doesn't allow this action.")
    async with request_guard(db, "/app-admin/storage/upload-url"):
        result = storage_service.request_admin_upload(domain=body.domain, content_type=body.content_type)
        return UploadResponse(**result)


@router.post("/auth/password/forgot", status_code=status.HTTP_204_NO_CONTENT)
async def admin_forgot_password_route(body: AdminForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/app-admin/auth/password/forgot"):
        await auth_service.request_password_reset(session=db, email=body.email)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def admin_reset_password_route(body: AdminResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/app-admin/auth/password/reset"):
        await auth_service.reset_password(session=db, reset_token=body.token, new_password=body.password)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Overview ─────────────────────────────────────────────────────────────────

@router.get("/overview", response_model=PlatformOverviewResponse)
async def overview_route(db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read"))):
    from app.models import Organization, SupportTicket, User
    from app.models.platform_event import PlatformEvent
    from sqlalchemy import func

    async with request_guard(db, "/app-admin/overview"):
        total_orgs = (await db.execute(select(func.count()).select_from(Organization))).scalar_one()
        active_orgs = (await db.execute(select(func.count()).select_from(Organization).where(Organization.is_active.is_(True)))).scalar_one()
        suspended_orgs = total_orgs - active_orgs
        total_employees = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        upcoming_events = (await db.execute(select(func.count()).select_from(PlatformEvent).where(PlatformEvent.status.in_(("scheduled", "ongoing"))))).scalar_one()
        total_events = (await db.execute(select(func.count()).select_from(PlatformEvent))).scalar_one()
        open_tickets = (await db.execute(select(func.count()).select_from(SupportTicket).where(SupportTicket.status.in_(("open", "in_progress"))))).scalar_one()

        return PlatformOverviewResponse(
            organizations={"total": total_orgs, "active": active_orgs, "suspended": suspended_orgs, "new_this_month": 0},
            employees={"total": total_employees},
            individuals={"total": 0, "active": 0, "new_this_month": 0},
            platform_events={"upcoming": upcoming_events, "total": total_events},
            support={"open": open_tickets},
            signup_trend=[],
        )


# ── Plans ────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans_route(
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    """The platform-wide plan catalogue — used to populate the plan picker
    for manual subscription activation. Mirrors GET /plans (org-facing),
    which admin tokens can't call since it requires an org-user token."""
    from app.services.payment.get_subscription import get_available_plans
    async with request_guard(db, "/app-admin/plans"):
        plans = await get_available_plans(session=db)
        return {"items": [{"id": p.id, "name": p.name, "billing_interval": p.billing_interval, "amount": p.amount, "currency": p.currency} for p in plans]}


# ── Organizations ──────────────────────────────────────────────────────────

@router.get("/organizations", response_model=PaginatedResponse)
async def list_organizations_route(
    q: str | None = Query(default=None), status_filter: str | None = Query(default=None, alias="status"),
    sort: str | None = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations"):
        items, total = await organization_service.list_organizations(session=db, q=q, status_filter=status_filter, sort=sort, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/organizations/{org_id}", response_model=OrganizationDetailOut)
async def get_organization_route(org_id: str, db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read"))):
    async with request_guard(db, "/app-admin/organizations/{org_id}"):
        return await organization_service.get_organization_detail(session=db, org_id=org_id)


@router.post("/organizations/{org_id}/suspend", response_model=OrganizationDetailOut)
async def suspend_organization_route(
    org_id: str, body: SuspendOrganizationRequest, request: Request,
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/organizations/{org_id}/suspend"):
        result = await organization_service.suspend_organization(session=db, org_id=org_id, reason=body.reason)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="organization.suspended",
            target_type="organization", target_id=org_id, target_label=result["name"], reason=body.reason, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.post("/organizations/{org_id}/reactivate", response_model=OrganizationDetailOut)
async def reactivate_organization_route(
    org_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/organizations/{org_id}/reactivate"):
        result = await organization_service.reactivate_organization(session=db, org_id=org_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="organization.reactivated",
            target_type="organization", target_id=org_id, target_label=result["name"], ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.post("/organizations/{org_id}/comp", response_model=OrganizationDetailOut)
async def grant_free_access_route(
    org_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/organizations/{org_id}/comp"):
        result = await organization_service.grant_free_access(session=db, org_id=org_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="organization.free_access_granted",
            target_type="organization", target_id=org_id, target_label=result["name"], ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.post("/organizations/{org_id}/comp/revoke", response_model=OrganizationDetailOut)
async def revoke_free_access_route(
    org_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/organizations/{org_id}/comp/revoke"):
        result = await organization_service.revoke_free_access(session=db, org_id=org_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="organization.free_access_revoked",
            target_type="organization", target_id=org_id, target_label=result["name"], ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.post("/organizations/{org_id}/subscription/activate", response_model=OrganizationDetailOut)
async def manually_activate_subscription_route(
    org_id: str, body: ActivateSubscriptionRequest, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/organizations/{org_id}/subscription/activate"):
        result = await organization_service.manually_activate_subscription(session=db, org_id=org_id, plan_id=body.plan_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="organization.subscription_manually_activated",
            target_type="organization", target_id=org_id, target_label=result["name"], reason=body.reason, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.get("/organizations/{org_id}/members", response_model=PaginatedResponse)
async def list_org_members_route(
    org_id: str, q: str | None = Query(default=None), status_filter: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations/{org_id}/members"):
        items, total = await organization_service.list_members(session=db, org_id=org_id, q=q, status_filter=status_filter, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/organizations/{org_id}/branches", response_model=PaginatedResponse)
async def list_org_branches_route(
    org_id: str, q: str | None = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations/{org_id}/branches"):
        items, total = await organization_service.list_branches(session=db, org_id=org_id, q=q, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/organizations/{org_id}/departments", response_model=PaginatedResponse)
async def list_org_departments_route(
    org_id: str, q: str | None = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations/{org_id}/departments"):
        items, total = await organization_service.list_departments(session=db, org_id=org_id, q=q, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/organizations/{org_id}/clubs", response_model=PaginatedResponse)
async def list_org_clubs_route(
    org_id: str, q: str | None = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations/{org_id}/clubs"):
        items, total = await organization_service.list_clubs(session=db, org_id=org_id, q=q, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/organizations/{org_id}/events", response_model=PaginatedResponse)
async def list_org_events_route(
    org_id: str, q: str | None = Query(default=None), status_filter: str | None = Query(default=None, alias="status"),
    sort: str | None = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations/{org_id}/events"):
        items, total = await organization_service.list_org_events(session=db, org_id=org_id, q=q, status_filter=status_filter, sort=sort, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/organizations/{org_id}/transactions", response_model=PaginatedResponse)
async def list_org_transactions_route(
    org_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/organizations/{org_id}/transactions"):
        items, total = await payment_service.list_org_transactions(session=db, org_id=org_id, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


# ── Payments ─────────────────────────────────────────────────────────────

@router.get("/payments/search", response_model=PaginatedResponse)
async def search_payments_route(
    q: str = Query(min_length=3), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/payments/search"):
        items, total = await payment_service.search_transactions(session=db, q=q, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


# ── User account actions ──────────────────────────────────────────────────

@router.get("/users/search", response_model=PaginatedResponse)
async def search_users_route(
    q: str = Query(min_length=2), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.read")),
):
    async with request_guard(db, "/app-admin/users/search"):
        items, total = await organization_service.search_users(session=db, q=q, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.post("/users/{user_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_user_route(
    user_id: str, body: SuspendUserRequest, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    from app.models import User, UserDevice
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/users/{user_id}/suspend"):
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            from fastapi import HTTPException
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        user.status = "suspended"
        await db.execute(UserDevice.__table__.update().where(UserDevice.user_id == user_id).values(revoked=True, refresh_token_hash=None))
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="user.suspended", target_type="user",
            target_id=user_id, target_label=f"{user.first_name} {user.last_name}".strip() or user.email, reason=body.reason, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/reactivate", status_code=status.HTTP_204_NO_CONTENT)
async def reactivate_user_route(
    user_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    from app.models import User
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/users/{user_id}/reactivate"):
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            from fastapi import HTTPException
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        user.status = "active"
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="user.reactivated", target_type="user",
            target_id=user_id, target_label=f"{user.first_name} {user.last_name}".strip() or user.email, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/password-reset", status_code=status.HTTP_204_NO_CONTENT)
async def admin_trigger_user_password_reset_route(
    user_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("organizations.manage")),
):
    from app.models import User
    from app.services.user_setting.password_service import request_password_reset as request_org_user_password_reset
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/users/{user_id}/password-reset"):
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            from fastapi import HTTPException
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        await request_org_user_password_reset(session=db, email=user.email)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="user.password_reset_sent", target_type="user",
            target_id=user_id, target_label=f"{user.first_name} {user.last_name}".strip() or user.email, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Platform events ────────────────────────────────────────────────────────

@router.get("/events", response_model=PaginatedResponse)
async def list_platform_events_route(
    q: str | None = Query(default=None), status_filter: str | None = Query(default=None, alias="status"),
    audience: str | None = Query(default=None), sort: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("events.read")),
):
    async with request_guard(db, "/app-admin/events"):
        items, total = await platform_event_service.list_platform_events(session=db, q=q, status_filter=status_filter, audience=audience, sort=sort, offset=offset, limit=limit)
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.post("/events", response_model=PlatformEventOut, status_code=status.HTTP_201_CREATED)
async def create_platform_event_route(
    body: PlatformEventCreateRequest, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("events.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/events"):
        result = await platform_event_service.create_platform_event(session=db, created_by=current_admin.admin_id, body=body.model_dump())
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="event.created", target_type="event",
            target_id=result["id"], target_label=result["title"], ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.get("/events/{event_id}", response_model=PlatformEventOut)
async def get_platform_event_route(event_id: str, db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("events.read"))):
    async with request_guard(db, "/app-admin/events/{event_id}"):
        return await platform_event_service.get_platform_event(session=db, event_id=event_id)


@router.patch("/events/{event_id}", response_model=PlatformEventOut)
async def update_platform_event_route(
    event_id: str, body: PlatformEventUpdateRequest, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("events.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/events/{event_id}"):
        result = await platform_event_service.update_platform_event(session=db, event_id=event_id, body=body.model_dump(exclude_unset=True))
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="event.updated", target_type="event",
            target_id=event_id, target_label=result["title"], ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.post("/events/{event_id}/cancel", response_model=PlatformEventOut)
async def cancel_platform_event_route(
    event_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("events.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/events/{event_id}/cancel"):
        result = await platform_event_service.cancel_platform_event(session=db, event_id=event_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="event.cancelled", target_type="event",
            target_id=event_id, target_label=result["title"], ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return result


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform_event_route(
    event_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("events.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/events/{event_id}"):
        event = await platform_event_service.get_platform_event_or_404(session=db, event_id=event_id)
        title = event.title
        await platform_event_service.delete_platform_event(session=db, event_id=event_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="event.deleted", target_type="event",
            target_id=event_id, target_label=title, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Admin team ───────────────────────────────────────────────────────────────

@router.get("/admins", response_model=PaginatedResponse)
async def list_admins_route(
    q: str | None = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("admins.manage")),
):
    async with request_guard(db, "/app-admin/admins"):
        rows, total = await admin_team_service.list_admins(session=db, q=q, offset=offset, limit=limit)
        return PaginatedResponse(items=[AdminUserOut.model_validate(a) for a in rows], total=total, offset=offset, limit=limit)


@router.post("/admins/invite", response_model=AdminUserOut)
async def invite_admin_route(
    body: InviteAdminRequest, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("admins.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/admins/invite"):
        admin = await admin_team_service.invite_admin(session=db, email=body.email, role=body.role, invited_by=current_admin.admin_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="admin.invited", target_type="admin",
            target_id=admin.id, target_label=admin.email, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return AdminUserOut.model_validate(admin)


@router.patch("/admins/{admin_id}", response_model=AdminUserOut)
async def update_admin_role_route(
    admin_id: str, body: UpdateAdminRoleRequest, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("admins.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/admins/{admin_id}"):
        admin = await admin_team_service.update_admin_role(session=db, admin_id=admin_id, new_role=body.role, acting_admin_id=current_admin.admin_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="admin.role_changed", target_type="admin",
            target_id=admin_id, target_label=admin.email, reason=f"New role: {body.role}", ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return AdminUserOut.model_validate(admin)


@router.delete("/admins/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_admin_route(
    admin_id: str, request: Request, db: AsyncSession = Depends(get_db),
    current_admin: CurrentAdmin = Depends(require_admin_permission("admins.manage")),
):
    ip, ua = _client_meta(request)
    async with request_guard(db, "/app-admin/admins/{admin_id}"):
        admin = (await db.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one()
        await admin_team_service.remove_admin(session=db, admin_id=admin_id, acting_admin_id=current_admin.admin_id)
        await log_admin_audit(
            session=db, actor_admin_id=current_admin.admin_id, action="admin.removed", target_type="admin",
            target_id=admin_id, target_label=admin.email, ip_address=ip, user_agent=ua,
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit-log", response_model=PaginatedResponse)
async def list_audit_log_route(
    q: str | None = Query(default=None), target_type: str | None = Query(default=None),
    start_date: date_type | None = Query(default=None, description="Inclusive — entries on or after this date (UTC)."),
    end_date: date_type | None = Query(default=None, description="Inclusive — entries on or before this date (UTC)."),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_admin: CurrentAdmin = Depends(require_admin_permission("audit.read")),
):
    from datetime import datetime, time, timezone
    from app.models import AuditEntry
    from sqlalchemy import func, or_
    from sqlalchemy.orm import selectinload

    async with request_guard(db, "/app-admin/audit-log"):
        query = select(AuditEntry).options(selectinload(AuditEntry.actor))
        count_query = select(func.count()).select_from(AuditEntry)
        if target_type:
            query, count_query = query.where(AuditEntry.target_type == target_type), count_query.where(AuditEntry.target_type == target_type)
        if q:
            pattern = f"%{q}%"
            query, count_query = query.where(AuditEntry.target_label.ilike(pattern)), count_query.where(AuditEntry.target_label.ilike(pattern))
        if start_date:
            floor = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
            query, count_query = query.where(AuditEntry.created_at >= floor), count_query.where(AuditEntry.created_at >= floor)
        if end_date:
            ceiling = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
            query, count_query = query.where(AuditEntry.created_at <= ceiling), count_query.where(AuditEntry.created_at <= ceiling)

        total = (await db.execute(count_query)).scalar_one()
        rows = (await db.execute(query.order_by(AuditEntry.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        items = [
            AuditEntryOut(
                id=e.id,
                actor=AuditActorOut(id=e.actor.id, name=f"{e.actor.first_name} {e.actor.last_name}".strip(), email=e.actor.email) if e.actor else None,
                action=e.action, target_type=e.target_type, target_id=e.target_id, target_label=e.target_label,
                reason=e.reason, created_at=e.created_at,
            )
            for e in rows
        ]
        return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)
