"""
app/services/organization/invite_service.py

Organization invite management.

Invite flow (send side):
  POST /organizations/{org_id}/invites
    → send_invite()
      1. Org active check
      2. Email not already registered check
      3. Duplicate pending invite check
      4. Resolve inviter's member.invite permission scope (org-wide vs branch-scoped)
      5. Branch must exist and belong to this org
      6. Department belongs to org AND to the target branch
      7. Role validity: system role, OR custom role owned by this exact branch
      7a. Privilege escalation gate — only super_admin/admin may invite
          someone into the admin or branch_manager role; super_admin
          itself can never be assigned via invite.
      8. Unique role check — against UserRoleAssignment, not User columns
      9. Scope gate — branch-scoped inviters can only invite to permitted branches
      10. Create OrganizationInvite row
      11. Send invite email

REVISED for the branch/role redesign:
  - There is no more "org-wide department" (Department.branch_id is now
    NOT NULL) — every department belongs to exactly one branch, so the
    department must simply match the invite's target branch_id.
  - Custom roles are now ALWAYS branch-owned (Role.branch_id is NOT
    NULL for any non-system role) and are isolated to that branch.
  - unique_scope='org' is REMOVED — Role.unique_scope only accepts
    'branch'/'department' now.
  - Unique role conflicts are checked against UserRoleAssignment, not
    User.role_id/branch_id/department_id.
  - Guard ordering: branch existence (Guard 5) runs before department
    and role checks since both compare against branch_id.

Permission model for member.invite:
  - A user can hold member.invite for multiple branches simultaneously —
    one UserPermission row per branch.
  - An org-wide grant (branch_id=NULL) supersedes all branch-level
    grants and allows inviting to any branch. When an org-wide grant is
    assigned the system removes all branch-level grants for the same
    permission automatically (enforced at assignment time, not here).
  - Guard 4 fetches ALL grants for this permission (no .limit(1)) and
    resolves them into either: org-wide (can invite anywhere), or a set
    of permitted branch IDs. Guard 9 then checks membership in that set.

Privilege escalation (Guard 7a):
  - member.invite lets a user invite people into their branch(es), but
    it must not let them hand out roles more powerful than (or equal
    to) the roles that actually govern a branch/org.
  - "admin" and "branch_manager" may ONLY be assigned by an inviter who
    is themselves super_admin or admin. A branch_manager (or anyone
    else) holding member.invite for their branch cannot invite a peer
    or superior into those roles.
  - "super_admin" can NEVER be assigned through the invite flow at all,
    regardless of who sends it — it is reserved for org creation /
    explicit platform-level promotion outside this path.
  - This assumes RoleName.ADMIN / RoleName.BRANCH_MANAGER /
    RoleName.SUPER_ADMIN correspond 1:1 with Role.name for system
    roles, and that custom (branch-owned) roles never reuse those
    reserved names. If custom roles can be named "admin" etc., this
    check should key off role.is_system + role.name instead, or a
    dedicated role level/rank field.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import InviteStatus, RoleName
from app.models import Organization, OrganizationInvite, Permission, Role, User, UserPermission
from app.models.branch import Branch
from app.models.department import Department
from app.models.permission import UserRoleAssignment
from app.services.errors import AuthError
from app.core.constants import INVITE_TTL_DAYS, ESCALATION_RESTRICTED_ROLES
from app.core.config import settings
from app.services.payment import enforce_plan_limit

logger = logging.getLogger(__name__)



_INVITE_CODE_LIMIT = 1_000_000
_INVITE_CODE_GENERATION_ATTEMPTS = 20


def _generate_invite_code() -> str:
    """Return a cryptographically secure, zero-padded six-digit code."""
    return f"{secrets.randbelow(_INVITE_CODE_LIMIT):06d}"


async def _generate_unique_invite_code(session: AsyncSession) -> str:
    """Generate a code that is not already assigned to another invite."""
    for _ in range(_INVITE_CODE_GENERATION_ATTEMPTS):
        code = _generate_invite_code()
        result = await session.execute(
            select(OrganizationInvite.id).where(OrganizationInvite.invite_code == code)
        )
        if result.scalar_one_or_none() is None:
            return code

    raise AuthError(
        "Could not generate a unique invite code. Please try again.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


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
    branch_id: str,
) -> OrganizationInvite:
    """
    Create and send an organization invite.

    Returns the created OrganizationInvite (flushed — caller commits).
    Raises AuthError on any validation failure.
    """
    email = invited_email.lower().strip()
    now = datetime.now(timezone.utc)
    is_super_admin = invited_by_role == RoleName.SUPER_ADMIN
    is_admin = invited_by_role == RoleName.ADMIN

   # ── Guard 1: org must exist and be active ──────────────────────────────
    org_result = await session.execute(select(Organization).where(Organization.id == org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise AuthError("Organization not found.", status.HTTP_404_NOT_FOUND)
    if not org.is_active:
        raise AuthError("Organization is deactivated. Invites cannot be sent.", status.HTTP_403_FORBIDDEN)

    # ── Guard 1a: plan seat limit ────────────────────────────────────────────
    # Checked early, before any other work — no point validating
    # branch/role/department if the org can't add another member at all.
    current_member_count = (await session.execute(
        select(func.count()).select_from(User).where(User.organization_id == org_id, User.email.notlike("%@deleted.wellstaq.invalid"))
    )).scalar_one()

    # Pending invites also count toward the seat limit — otherwise an
    # admin could send 100 invites in a row past their limit, since
    # none of them are "members" yet until accepted.
    pending_invite_count = (await session.execute(
        select(func.count()).select_from(OrganizationInvite).where(
            OrganizationInvite.organization_id == org_id,
            OrganizationInvite.status == InviteStatus.PENDING,
            OrganizationInvite.expires_at > now,
        )
    )).scalar_one()

    await enforce_plan_limit(
        session=session, organization_id=org_id, limit_key="max_employees",
        current_count=current_member_count + pending_invite_count,
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
            "This person already has a pending invitation. Ask them to check their inbox and spam folder, or wait for the invitation to expire before sending another one.",
            status.HTTP_409_CONFLICT,
        )

    # ── Guard 4: resolve inviter's member.invite permission scope ──────────────
    #
    # A user may hold member.invite for multiple branches simultaneously —
    # one UserPermission row per branch. We fetch ALL rows (no .limit(1))
    # and resolve them as follows:
    #
    #   • Any row with branch_id=NULL → org-wide grant → can invite to any
    #     branch. invite_is_org_wide=True, permitted_branch_ids unused.
    #
    #   • All rows branch-scoped → collect branch IDs into a set.
    #     Guard 9 checks that the target branch_id is in this set.
    #
    # The previous .limit(1) bug: if a user had grants for Branch A and
    # Branch B, the query returned whichever came first. Guard 9 then
    # blocked the user from inviting to the other branch even though they
    # had a legitimate grant for it.
    invite_is_org_wide: bool = False
    permitted_branch_ids: set[str] = set()

    if not is_super_admin:
        grants = [grant for grant in invited_by_permissions if getattr(grant, "name", grant) == "member.invite"]

        if not grants:
            raise AuthError(
                "You do not have permission to send invites.",
                status.HTTP_403_FORBIDDEN,
            )

        # Org-wide grant (branch_id=NULL) supersedes all branch-level grants.
        if any(getattr(g, "branch_id", None) is None for g in grants):
            invite_is_org_wide = True
        else:
            permitted_branch_ids = {getattr(g, "branch_id", None) for g in grants if getattr(g, "branch_id", None)}

    # ── Guard 5: branch must exist and belong to this org ─────────────────────
    # Validated before Guards 6 and 7 because both compare against
    # branch_id — an invalid branch_id would otherwise produce misleading
    # "belongs to a different branch" errors instead of a clean 404.
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

    # ── Guard 6: department must exist, belong to org, AND match target branch ─
    # Department.branch_id is NOT NULL — every department belongs to exactly
    # one branch. It must be the same branch this invite targets.
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

    if department.branch_id != branch_id:
        raise AuthError(
            "The assigned department belongs to a different branch than "
            "this invite targets.",
            status.HTTP_400_BAD_REQUEST,
        )

    # ── Guard 7: role validity + branch isolation ───────────────────────────────
    role_result = await session.execute(
        select(Role).where(Role.id == invited_role_id)
    )
    role = role_result.scalar_one_or_none()
    if role is None:
        raise AuthError("Role not found.", status.HTTP_404_NOT_FOUND)

    if role.is_system or role.organization_id != org_id:
        raise AuthError(
            "Select a role belonging to this organization.",
            status.HTTP_400_BAD_REQUEST,
        )

    # ── Guard 7a: privilege escalation gate ─────────────────────────────────────
    #
    # super_admin can never be assigned via invite, regardless of who is
    # sending it — reserved for org creation / explicit platform-level
    # promotion.
    #
    # admin and branch_manager can only be granted by an inviter who is
    # themselves super_admin or admin. A branch-scoped inviter (e.g. a
    # branch_manager with member.invite for their own branch) cannot use
    # that grant to create a peer or superior role.
    if role.name == RoleName.SUPER_ADMIN:
        raise AuthError(
            'The "super_admin" role cannot be assigned through an invite.',
            status.HTTP_403_FORBIDDEN,
        )

    if role.name in ESCALATION_RESTRICTED_ROLES and not (is_super_admin or is_admin):
        raise AuthError(
            f'Only super admins or admins can invite users as "{role.name}".',
            status.HTTP_403_FORBIDDEN,
        )

    # ── Guard 8: unique role check — against UserRoleAssignment ────────────────
    #
    # unique_scope='branch':
    #   One active holder per branch. Conflict: any UserRoleAssignment
    #   for this role scoped to the TARGET BRANCH.
    #
    # unique_scope='department':
    #   One active holder per department. Conflict: any UserRoleAssignment
    #   for this role scoped to the TARGET DEPARTMENT.
    #
    # There is no 'org' scope — see module docstring.
    if role.is_unique:
        if role.unique_scope == "branch":
            conflict = await session.execute(
                select(UserRoleAssignment.id).where(
                    UserRoleAssignment.role_id == invited_role_id,
                    UserRoleAssignment.branch_id == branch_id,
                )
            )
            if conflict.scalar_one_or_none() is not None:
                raise AuthError(
                    f'The role "{role.name}" is already held by someone in '
                    "this branch.",
                    status.HTTP_409_CONFLICT,
                )

        elif role.unique_scope == "department":
            conflict = await session.execute(
                select(UserRoleAssignment.id).where(
                    UserRoleAssignment.role_id == invited_role_id,
                    UserRoleAssignment.department_id == invited_department_id,
                )
            )
            if conflict.scalar_one_or_none() is not None:
                raise AuthError(
                    f'The role "{role.name}" is already held by someone in '
                    "this department.",
                    status.HTTP_409_CONFLICT,
                )

        else:
            # Data integrity error — the DB CheckConstraint should prevent
            # unique_scope from being anything other than 'branch' or
            # 'department' when is_unique=True.
            raise AuthError(
                f'Role "{role.name}" is marked unique but has an '
                f'unrecognized unique_scope "{role.unique_scope}". '
                "Contact your platform administrator.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Guard 9: scope gate ───────────────────────────────────────────────────
    #
    # super_admin bypasses entirely.
    # org-wide grant (invite_is_org_wide=True) bypasses entirely.
    # branch-scoped: target branch_id must be in the set of branches
    #   the inviter holds member.invite for. This correctly handles
    #   users with grants for multiple branches — any permitted branch
    #   is valid, not just the first one returned by a query.
    if not is_super_admin and not invite_is_org_wide:
        if branch_id not in permitted_branch_ids:
            raise AuthError(
                "Your invite permission does not cover this branch. "
                "You can only invite users into branches you are authorised to manage.",
                status.HTTP_403_FORBIDDEN,
            )

    # ── Create invite ──────────────────────────────────────────────────────────
    invite_code = await _generate_unique_invite_code(session)

    invite = OrganizationInvite(
        organization_id=org_id,
        branch_id=branch_id,
        invited_department_id=invited_department_id,
        invited_role_id=invited_role_id,
        invited_by=invited_by_user_id,
        invite_code=invite_code,
        invited_email=email,
        status=InviteStatus.PENDING,
        expires_at=now + timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(invite)
    await session.flush()

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
    Send the invite email via transactional email.
    Raises AuthError on delivery failure so the caller can rollback —
    an undelivered invite should leave no DB trace.
    """
    invite_url = f"{settings.PUBLIC_APP_ORIGIN.rstrip('/')}/invite/register?code={invite_code}"

    html_body = f"""
    <html>
      <body style="font-family:sans-serif;color:#333;max-width:520px;margin:auto;">
        <h2 style="margin-bottom:4px;">You've been invited to join {org_name}</h2>
        <p style="color:#555;">
          You have been invited to join <strong>{org_name}</strong> as a
          <strong>{role_name}</strong> in the <strong>{department_name}</strong> department.
        </p>
        <p style="color:#555;">Your six-digit invite code is:</p>
        <div style="display:inline-block;padding:12px 20px;background:#f4f4f4;
          border-radius:8px;font-size:30px;font-weight:bold;letter-spacing:8px;
          margin:8px 0 20px;">{invite_code}</div>
        <p style="color:#555;">
          Click the button below to accept your invite and set up your account.
          This link expires in <strong>{expires_days} days</strong>.
          Or copy this link to your browser: <strong>{invite_url}</strong>
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
