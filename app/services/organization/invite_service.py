"""
app/services/organization/invite_service.py

Organization invite management.

Responsibilities:
  - Validate the inviter has authority to invite to the target branch/org
  - Validate the invited email is not already a member
  - Validate the department and role belong to the org
  - Generate a secure invite code
  - Create the OrganizationInvite row
  - Send the invite email

Services never commit — the router owns the transaction boundary.

Invite flow (send side):
  POST /organizations/{org_id}/invites
    → send_invite()
      1. Org active check
      2. Email not already registered check
      3. Duplicate pending invite check
      4. Department belongs to org check
      5. Role belongs to org (or is system role) check
      6. Branch belongs to org check (if branch_id provided)
      7. Scope guard — inviter can only invite into their own branch
         unless they hold member.invite org-wide
      8. Create OrganizationInvite row
      9. Send invite email
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import InviteStatus, RoleName
from app.models import Organization, OrganizationInvite, Role, User
from app.models.department import Department
from app.services.errors import AuthError

logger = logging.getLogger(__name__)

# How long an invite link stays valid
INVITE_TTL_DAYS = 7


def _generate_invite_code() -> str:
    """
    Cryptographically secure URL-safe invite code.
    32 bytes → 64 hex chars — vanishingly unlikely to collide.
    """
    return secrets.token_urlsafe(32)

async def send_invite(
    session: AsyncSession,
    *,
    org_id: str,
    invited_by_user_id: str,
    invited_by_role: str | None,
    invited_by_branch_id: str | None,
    invited_by_permissions: list[str],
    invited_email: str,
    invited_department_id: str,
    invited_role_id: str,
    branch_id: str,  # ── now required, not Optional ────────────────────────────
) -> OrganizationInvite:
    """
    Create and send an organization invite.

    Parameters
    ──────────
    org_id                  — the organization the invite belongs to
    invited_by_user_id      — current_user.user_id
    invited_by_role         — current_user.role (for scope checks)
    invited_by_branch_id    — current_user.branch_id (for scope checks)
    invited_by_permissions  — current_user.permissions (for scope checks)
    invited_email           — recipient email (lowercased + stripped here)
    invited_department_id   — department the invitee will join
    invited_role_id         — role the invitee will receive on joining
    branch_id               — REQUIRED. Every invite must assign the invitee
                               to a branch — there is no org-level-only invite.

    Returns the created OrganizationInvite (unflushed — caller flushes).

    Raises AuthError on any validation failure.
    """
    email = invited_email.lower().strip()
    now = datetime.now(timezone.utc)

    # ── Guard 1: org must exist and be active ──────────────────────────────────
    org_result = await session.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = org_result.scalar_one_or_none()
    if org is None:
        raise AuthError("Organization not found.", status.HTTP_404_NOT_FOUND)
    if not org.is_active:
        raise AuthError(
            "Organization is deactivated. Invites cannot be sent.",
            status.HTTP_403_FORBIDDEN,
        )

    # ── Guard 2: email must not already be a member ────────────────────────────
    existing_user = await session.execute(
        select(User).where(User.email == email)
    )
    if existing_user.scalar_one_or_none():
        raise AuthError(
            "A user with this email is already registered on the platform.",
            status.HTTP_409_CONFLICT,
        )

    # ── Guard 3: no duplicate pending invite for this email + org ─────────────
    duplicate = await session.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.organization_id == org_id,
            OrganizationInvite.invited_email == email,
            OrganizationInvite.status == InviteStatus.PENDING,
            OrganizationInvite.expires_at > now,
        )
    )
    if duplicate.scalar_one_or_none():
        raise AuthError(
            "A pending invite has already been sent to this email address.",
            status.HTTP_409_CONFLICT,
        )

    # ── Guard 4: department must exist and belong to this org ──────────────────
    dept_result = await session.execute(
        select(Department).where(
            Department.id == invited_department_id,
            Department.organization_id == org_id,
        )
    )
    department = dept_result.scalar_one_or_none()
    if department is None:
        raise AuthError(
            "Department not found or does not belong to this organization.",
            status.HTTP_404_NOT_FOUND,
        )

    # ── Guard 5: role must be a system role or belong to this org ─────────────
    role_result = await session.execute(
        select(Role).where(Role.id == invited_role_id)
    )
    role = role_result.scalar_one_or_none()
    if role is None:
        raise AuthError("Role not found.", status.HTTP_404_NOT_FOUND)

    if not role.is_system and role.organization_id != org_id:
        raise AuthError(
            "Role does not belong to this organization.",
            status.HTTP_400_BAD_REQUEST,
        )

    # ── Guard 6: branch must exist and belong to this org ──────────────────────
    from app.models.branch import Branch  # local import avoids circular
    branch_result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == org_id,
        )
    )
    if branch_result.scalar_one_or_none() is None:
        raise AuthError(
            "Branch not found or does not belong to this organization.",
            status.HTTP_404_NOT_FOUND,
        )

    # If the role is branch-scoped, it must match the invite's branch.
    if role.branch_id is not None and role.branch_id != branch_id:
        raise AuthError(
            "The assigned role is scoped to a different branch.",
            status.HTTP_400_BAD_REQUEST,
        )

    # ── Guard 7: scope check — inviter can only invite into their own branch ───
    # super_admin bypasses this — they can invite into any branch.
    # Everyone else must either hold member.invite org-wide, or be a
    # member of the target branch themselves.
    if invited_by_role != RoleName.SUPER_ADMIN:
        has_org_wide_invite = "member.invite" in invited_by_permissions
        if not has_org_wide_invite and invited_by_branch_id != branch_id:
            raise AuthError(
                "You can only invite users into your own branch.",
                status.HTTP_403_FORBIDDEN,
            )

    # ── Create invite ──────────────────────────────────────────────────────────
    invite_code = _generate_invite_code()
    print(f"The virtues of life if I have the choice of life  I should be able to feel everyday with pleasure {invite_code}")
    invite = OrganizationInvite(
        organization_id=org_id,
        branch_id=branch_id,
        invited_department_id=invited_department_id,
        invited_role_id=invited_role_id,
        invited_by=invited_by_user_id,
        invite_code=invite_code,
        invited_email=email,          # already lowercased + stripped above
        status=InviteStatus.PENDING,
        expires_at=now + timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(invite)
    await session.flush()  # get invite.id before sending email

    # ── Send invite email ──────────────────────────────────────────────────────
    await _send_invite_email(
        email=email,
        invite_code=invite_code,
        org_name=org.business_name,
        department_name=department.name,
        role_name=role.name,
        expires_days=INVITE_TTL_DAYS,
    )

    logger.info(
        "Invite sent",
        extra={
            "invite_id": invite.id,
            "org_id": org_id,
            "invited_email": email,
            "invited_by": invited_by_user_id,
            "branch_id": branch_id,
            "department_id": invited_department_id,
            "role_id": invited_role_id,
        },
    )

    return invite


async def _send_invite_email(
    *,
    email: str,
    invite_code: str,
    org_name: str,
    department_name: str,
    role_name: str,
    expires_days: int,
) -> None:
    """
    Send the invite email via SendGrid.
    The invite link embeds the invite_code — the frontend uses it to
    call POST /auth/invite/verify-otp and then POST /auth/invite/accept.

    Raises AuthError on delivery failure so the caller can rollback —
    an undelivered invite should leave no DB trace.
    """

    #TODO
    # Frontend invite acceptance URL — update to match your actual domain
    invite_url = f"https://app.wellstaq.com/invite?code={invite_code}"

    html_body = f"""
    <html>
      <body style="font-family:sans-serif;color:#333;max-width:520px;margin:auto;">
        <h2 style="margin-bottom:4px;">You've been invited to join {org_name}</h2>
        <p style="color:#555;">
          You have been invited to join <strong>{org_name}</strong> as a
          <strong>{role_name}</strong> in the <strong>{department_name}</strong> department.
        </p>
        <p style="color:#555;">
          Click the button below to accept your invite and set up your account.
          This link expires in <strong>{expires_days} days</strong>.
        </p>
        <a href="{invite_url}" style="
          display:inline-block;padding:12px 28px;
          background:#4F46E5;color:#fff;border-radius:8px;
          font-size:16px;font-weight:600;text-decoration:none;
          margin:20px 0;
        ">Accept Invite</a>
        <p style="color:#999;font-size:12px;">
          If you weren't expecting this invite, you can safely ignore this email.
        </p>
      </body>
    </html>
    """

    try:
        from app.core.email import send_email
        await send_email(
            to=email,
            subject=f"You've been invited to join {org_name} on Wellstaq",
            html=html_body,
        )
    except Exception:
        logger.error(
            "Failed to send invite email",
            exc_info=True,
            extra={"recipient": email},
        )
        raise AuthError(
            "Failed to send invite email. Please try again later.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )