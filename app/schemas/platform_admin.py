"""
app/schemas/platform_admin.py

Wire shapes for /app-admin/* — field names and nesting match
docs/ADMIN_API_CONTRACT.md on the admin console repo (apps/admin)
exactly, since the console's TypeScript types were written against it
directly. snake_case on the wire (FastAPI's default from these field
names); the console's services/http.ts converts to/from camelCase.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as time_cls
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Auth ─────────────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AdminTwoFaRequiredResponse(BaseModel):
    two_fa_required: bool = True
    two_fa_challenge_token: str
    two_fa_method: str


class AdminTwoFaVerifyRequest(BaseModel):
    challenge_token: str
    code: str


class AdminRefreshRequest(BaseModel):
    refresh_token: str


class AdminForgotPasswordRequest(BaseModel):
    email: str


class AdminResetPasswordRequest(BaseModel):
    token: str
    password: str


class AdminMeResponse(BaseModel):
    id: str
    email: str
    first_name: str | None
    last_name: str | None
    role: str
    permissions: list[str]
    avatar_url: str | None
    two_fa_enabled: bool

    model_config = ConfigDict(from_attributes=True)


class UpdateAdminProfileRequest(BaseModel):
    """Self-service only — the router derives which admin from the
    access token, never a path param, so there's no way for one admin
    to edit another's name or photo through this endpoint."""
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    avatar_url: str | None = None


# ── Overview ─────────────────────────────────────────────────────────────────

class OverviewCount(BaseModel):
    total: int
    active: int | None = None
    suspended: int | None = None
    new_this_month: int | None = None


class SignupTrendPoint(BaseModel):
    date: date
    organizations: int
    individuals: int
    employees: int


class PlatformOverviewResponse(BaseModel):
    organizations: dict
    employees: dict
    individuals: dict
    platform_events: dict
    support: dict
    signup_trend: list[SignupTrendPoint] = Field(default_factory=list)


# ── Organizations ──────────────────────────────────────────────────────────

class OrganizationOwnerOut(BaseModel):
    name: str
    email: str


class OrganizationSummaryOut(BaseModel):
    id: str
    name: str
    logo_url: str | None
    industry: str | None
    country: str | None
    status: str
    work_model: str | None
    employee_count: int
    branch_count: int
    plan_name: str | None
    subscription_status: str | None
    platform_comped: bool
    owner: OrganizationOwnerOut | None
    created_at: datetime


class OrganizationDetailOut(OrganizationSummaryOut):
    business_email: str | None
    phone_number: str | None
    website: str | None
    address: str | None
    city: str | None
    state: str | None
    size_range: str | None
    counts: dict
    suspended_at: datetime | None
    suspension_reason: str | None


class SuspendOrganizationRequest(BaseModel):
    reason: str = Field(..., min_length=10)


class ActivateSubscriptionRequest(BaseModel):
    plan_id: str
    reason: str = Field(..., min_length=10, description="Why this is being activated manually, outside the normal checkout flow — e.g. a Paystack-confirmed payment whose webhook never arrived.")


class CancelSubscriptionRequest(BaseModel):
    reason: str = Field(..., min_length=10, description="Why the organization's subscription is being ended immediately by a platform administrator.")


class OrganizationMemberOut(BaseModel):
    id: str
    first_name: str | None
    last_name: str | None
    email: str
    avatar_url: str | None
    branch_name: str | None
    department_name: str | None
    role: str | None
    status: str
    joined_at: datetime | None


class OrganizationBranchOut(BaseModel):
    id: str
    name: str
    city: str | None
    country: str | None
    member_count: int
    manager_name: str | None


class OrganizationDepartmentOut(BaseModel):
    id: str
    name: str
    branch_name: str | None
    member_count: int
    head_name: str | None


class OrganizationClubOut(BaseModel):
    id: str
    name: str
    description: str | None
    image_url: str | None
    branch_name: str | None
    member_count: int
    created_at: datetime


class OrganizationEventOut(BaseModel):
    id: str
    title: str
    description: str | None
    image_url: str | None
    start_date: date
    end_date: date | None
    time: time_cls
    status: str
    branch_name: str | None
    participant_count: int
    created_at: datetime


class PaginatedResponse(BaseModel):
    items: list
    total: int
    offset: int
    limit: int


# ── User account actions ──────────────────────────────────────────────────

class SuspendUserRequest(BaseModel):
    reason: str = Field(..., min_length=10)


# ── Platform events ────────────────────────────────────────────────────────

class PlatformEventCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None
    start_date: date
    end_date: date | None = None
    time: time_cls
    timezone: str = "Africa/Lagos"
    audience: str
    organization_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_audience(self):
        if self.audience not in ("everyone", "individuals", "employees", "selected_organizations"):
            raise ValueError("Invalid audience.")
        if self.audience == "selected_organizations" and not self.organization_ids:
            raise ValueError("organization_ids must include at least one organization for audience='selected_organizations'.")
        return self


class PlatformEventUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    time: time_cls | None = None
    timezone: str | None = None
    audience: str | None = None
    organization_ids: list[str] | None = None


class PlatformEventOrgOut(BaseModel):
    id: str
    name: str


class PlatformEventCreatorOut(BaseModel):
    id: str
    name: str


class PlatformEventOut(BaseModel):
    id: str
    title: str
    description: str | None
    image_url: str | None
    start_date: date
    end_date: date | None
    time: time_cls
    timezone: str
    recurrence_rule: str | None
    status: str
    audience: str
    organizations: list[PlatformEventOrgOut]
    participant_count: int
    created_by: PlatformEventCreatorOut | None
    created_at: datetime
    updated_at: datetime | None


# ── Support tickets (admins listing) ───────────────────────────────────────

class AdminUserOut(BaseModel):
    id: str
    first_name: str | None
    last_name: str | None
    email: str
    role: str
    status: str
    two_fa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InviteAdminRequest(BaseModel):
    email: str
    role: str


class UpdateAdminRoleRequest(BaseModel):
    role: str


# ── Audit log ──────────────────────────────────────────────────────────────

class AuditActorOut(BaseModel):
    id: str
    name: str
    email: str


class AuditEntryOut(BaseModel):
    id: str
    actor: AuditActorOut | None
    action: str
    target_type: str
    target_id: str
    target_label: str
    reason: str | None
    created_at: datetime
