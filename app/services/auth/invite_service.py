"""
app/services/auth/invite_service.py

Invite-based registration flows:
  1. request_invite_otp  — validate invite, send OTP
  2. verify_invite_otp   — verify OTP, return invite_verification_token
  3. invite_register     — complete registration with wellbeing baseline

Employee registration flow:
  - User created with status=PENDING
  - Wellbeing baseline + priorities recorded
  - DepartmentMember row created to assign user to their department
  - UserRoleAssignment row created IF the invited role has a
    unique_scope ('branch' or 'department') — see step 7b below. Roles
    with unique_scope=NULL (e.g. "member") get no assignment row;
    User.role_id alone is sufficient for them.
  - User activated (ACTIVE) atomically before tokens issued

Key design notes:
  - User has NO department_id column. Department membership is tracked
    via the DepartmentMember join table (one row per user, enforced by
    UniqueConstraint("user_id") on DepartmentMember).
  - User.role_id is the user's single role label. WHERE that role is
    exercised (which branch/department) is tracked separately via
    UserRoleAssignment — see permission.py for the full model.

Privilege escalation (Step 7a guard):
  - send_invite() in the organization invite service already blocks
    super_admin invites from ever being created, and restricts
    admin/branch_manager invites to super_admin/admin senders. This is
    a defensive second checkpoint at registration time, in case a
    role's name changes, or an invite row is otherwise mutated, between
    when the invite was created and when it's accepted. The
    "super_admin" role can never be assigned via this flow — full stop.

Governance linking (Step 7c):
  - Some roles don't just get a User.role_id / UserRoleAssignment row —
    they also own a slot on another model. branch_manager is the
    current example: a branch's manager is tracked via
    Branch.manager_id (see app/services/branch/update_branch.py), which
    is completely separate from the role/assignment bookkeeping above.
    Without this step, a user could register with role=branch_manager,
    show up correctly everywhere role-based, and yet Branch.manager_id
    would stay NULL until someone separately called
    assign_branch_manager() — leaving the branch effectively unmanaged
    despite having a "manager".
  - _ROLE_GOVERNANCE_LINKERS is a small dispatch table so this
    generalizes to future roles with the same shape (e.g. a department
    head, once/if that model gains an owner FK) without editing
    invite_register() itself — just add a linker function and a table
    entry.
  - Each linker reuses the exact same underlying logic
    (seed_role_permissions_for_branch) that the manual
    assign_branch_manager() endpoint uses, so a branch_manager ends up
    in an identical state whether they arrived via invite or manual
    assignment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.core.constants import (
    ActivityType,
    InviteStatus,
    OAuthProvider,
    ReferenceType,
    RoleName,
    UserStatus,
)
from app.models import (
    User,
    UserAuthProvider,
    UserPriority,
    UserWellbeingBaseline,
)
from app.models.branch import Branch
from app.models.department import DepartmentMember
from app.models.permission import Role, UserRoleAssignment
from app.services.branch.update_branch import seed_role_permissions_for_branch
from .token_service import issue_tokens
from ._helpers import (
    AuthError,
    _create_user_settings,
    _decode_invite_verification_token,
    _load_pending_invite,
    _log_audit,
    _make_invite_verification_token,
    _mask_email,
    _validate_password_strength,

)


async def request_invite_otp(session: AsyncSession, invite_code: str) -> str:
    """
    Validate the invite and send an OTP to the invite's email.
    Returns masked email: "j***@example.com".
    """
    from .otp_service import request_otp

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)

    existing = await session.execute(
        select(User.id).where(User.email == invite.invited_email.lower())
    )
    if existing.scalar_one_or_none():
        raise AuthError(
            "An account already exists for this email address.",
            status.HTTP_409_CONFLICT,
        )

    await request_otp(session, invite.invited_email, purpose="verification")
    return _mask_email(invite.invited_email)


async def verify_invite_otp(
    session: AsyncSession, invite_code: str, code: str
) -> str:
    """
    Verify the OTP for an invite. Returns an invite_verification_token.
    """
    from .otp_service import verify_otp

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)
    await verify_otp(session, invite.invited_email, code)
    return _make_invite_verification_token(invite.invited_email, invite_code)


async def _create_role_assignment_if_needed(
    session: AsyncSession,
    *,
    user: User,
    role: Role,
    organization_id: str,
    branch_id: str,
    department_id: str,
) -> None:
    """
    Create a UserRoleAssignment row for the newly-registered user, IF
    the invited role is one that needs to track "where" (unique_scope
    is set). Roles with unique_scope=NULL (e.g. "member") get no row —
    User.role_id alone is sufficient for them; there's no oversight
    context to disambiguate.

    unique_scope="branch"     -> assignment scoped to branch_id
    unique_scope="department" -> assignment scoped to department_id

    Uniqueness (role.is_unique) is enforced HERE at the service layer,
    not via a DB constraint — see permission.py's UserRoleAssignment
    docstring for why a blanket DB unique index would be wrong (it
    would also block ordinary non-unique roles like "member").

    Raises AuthError (409) if role.is_unique and someone already holds
    this role in the target branch/department — this should be a rare
    race in practice, since send_invite() already validated role/branch/
    department consistency at invite-creation time, but a second invite
    for the same unique slot could still be accepted concurrently.
    """
    if role.unique_scope is None:
        return  # e.g. "member" — no assignment row needed

    if role.unique_scope == "branch":
        scope_filter = UserRoleAssignment.branch_id == branch_id
        assignment_kwargs = dict(branch_id=branch_id, department_id=None)
        conflict_msg = "This role is already held by someone else in this branch."
    elif role.unique_scope == "department":
        scope_filter = UserRoleAssignment.department_id == department_id
        assignment_kwargs = dict(branch_id=None, department_id=department_id)
        conflict_msg = "This role is already held by someone else in this department."
    else:
        # Defensive — the DB CheckConstraint already restricts
        # unique_scope to ('branch', 'department', NULL), so this
        # should be unreachable outside of a data-integrity bug.
        raise AuthError(
            f"Role has an unrecognized unique_scope: {role.unique_scope!r}.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if role.is_unique:
        existing = await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.role_id == role.id,
                scope_filter,
            )
        )
        if existing.scalar_one_or_none():
            raise AuthError(conflict_msg, status.HTTP_409_CONFLICT)

    session.add(UserRoleAssignment(
        user_id=user.id,
        role_id=role.id,
        organization_id=organization_id,
        **assignment_kwargs,
    ))


async def _link_branch_manager(
    session: AsyncSession,
    *,
    user: User,
    organization_id: str,
    branch_id: str,
    department_id: str | None = None,
) -> None:
    """
    Wire a newly-registered branch_manager into Branch.manager_id and
    seed them the standard branch-level permission set.

    This mirrors what assign_branch_manager() does for manual
    assignment via the branch endpoints (see
    app/services/branch/update_branch.py) — without this step a user
    ends up with role_id/UserRoleAssignment pointing at
    "branch_manager", but Branch.manager_id stays NULL until someone
    manually assigns them. That's the bug this closes.

    send_invite()'s unique-role check (Guard 8, unique_scope='branch')
    already prevents two branch_manager invites from coexisting for the
    same branch, so branch.manager_id should be NULL by the time we get
    here. We still guard defensively rather than silently overwriting
    an existing manager or silently doing nothing.
    """
    branch_result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    branch = branch_result.scalar_one_or_none()
    if branch is None:
        # send_invite() already validated the branch at invite-creation
        # time; reaching here means it was deleted after the invite was
        # sent but before it was accepted.
        raise AuthError(
            "The branch for this invite no longer exists. Please ask "
            "for a new invite.",
            status.HTTP_409_CONFLICT,
        )

    if branch.manager_id is not None and branch.manager_id != user.id:
        raise AuthError(
            "This branch already has a manager assigned. Please contact "
            "your organization administrator.",
            status.HTTP_409_CONFLICT,
        )

    branch.manager_id = user.id
    await session.flush()

    await seed_role_permissions_for_branch(
        session,
        user_id=user.id,
        organization_id=organization_id,
        branch_id=branch_id,
    )


# Roles that require additional "governance" wiring beyond
# User.role_id / UserRoleAssignment at registration time — i.e. roles
# that ALSO own a slot on some other model (Branch.manager_id today;
# a future Department "head" FK would be added the same way).
#
# Every linker shares the same keyword-argument signature so the
# dispatch call site never needs to change when a new role is added
# here — even if a given linker doesn't use every argument.
_ROLE_GOVERNANCE_LINKERS: dict[
    str, Callable[..., Awaitable[None]]
] = {
    RoleName.BRANCH_MANAGER: _link_branch_manager,
}


async def _apply_role_governance_linking(
    session: AsyncSession,
    *,
    role: Role,
    user: User,
    organization_id: str,
    branch_id: str,
    department_id: str,
) -> None:
    """
    Dispatch to the role-specific governance linker for this role, if
    one is registered. No-op for ordinary roles (e.g. "member") that
    don't own a slot on another model.
    """
    linker = _ROLE_GOVERNANCE_LINKERS.get(role.name)
    if linker is None:
        return

    await linker(
        session,
        user=user,
        organization_id=organization_id,
        branch_id=branch_id,
        department_id=department_id,
    )


async def invite_register(
    session: AsyncSession,
    invite_verification_token: str,
    first_name: str,
    last_name: str,
    password: str,
    country: str,
    state: str,
    baseline: dict,
    priorities: list[dict],
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    Complete invite registration with wellbeing baseline.

    Flow:
      1.  Decode token -> email, invite_code
      2.  Re-validate invite
      3.  Token substitution guard
      4.  Duplicate email guard
      5.  Create User (PENDING — not yet active)
      6.  Ancillary records: auth provider, settings
      7.  DepartmentMember row — assigns user to invited_department_id
      7a. Role lookup + super_admin guard — defensive check that the
          invited role isn't super_admin (see module docstring)
      7b. UserRoleAssignment row — IF the invited role needs one
          (unique_scope set) — see _create_role_assignment_if_needed
      7c. Governance linking — IF this role owns a slot on another
          model (e.g. branch_manager -> Branch.manager_id) — see
          _apply_role_governance_linking / _ROLE_GOVERNANCE_LINKERS
      8.  Wellbeing baseline rows
      9.  Priority rows
      10. Activate user (ACTIVE) + mark invite accepted + audit
      11. Issue tokens
    """
    # ── Step 1 ─────────────────────────────────────────────────────────────────
    email, invite_code = _decode_invite_verification_token(invite_verification_token)
    email = email.lower().strip()

    # ── Step 2 ─────────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)

    # ── Step 3: token substitution guard ──────────────────────────────────────
    if invite.invited_email.lower().strip() != email:
        raise AuthError("Invalid or expired invite verification token.")

    # ── Step 4: duplicate email guard ─────────────────────────────────────────
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise AuthError(
            "An account already exists for this email.",
            status.HTTP_409_CONFLICT,
        )
     # ── Step 4a: password strength ────────────────────────────────────────────
    _validate_password_strength(password)

    # ── Step 5: create user as PENDING ────────────────────────────────────────
    user = User(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email,
        hashed_password=hash_password(password),
        organization_id=invite.organization_id,
        branch_id=invite.branch_id,        # now always required — home branch
        role_id=invite.invited_role_id,
        country=country.strip() if country else None,
        state=state.strip() if state else None,
        status=UserStatus.PENDING,
    )
    session.add(user)
    await session.flush()  # need user.id for all child records below

    # ── Step 6: ancillary records ──────────────────────────────────────────────
    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=user.organization_id,
        provider=OAuthProvider.PASSWORD,
        provider_user_id=None,
        access_token=None,
        refresh_token=None,
    ))
    session.add(_create_user_settings(user))

    # ── Step 7: department membership ─────────────────────────────────────────
    session.add(DepartmentMember(
        department_id=invite.invited_department_id,
        user_id=user.id,
    ))

    # ── Step 7a: role lookup + super_admin guard ────────────────────────────────
    role_result = await session.execute(select(Role).where(Role.id == invite.invited_role_id))
    role = role_result.scalar_one_or_none()
    if role is None:
        # send_invite() already validated this at invite-creation time;
        # reaching here means the role was deleted after the invite was
        # sent but before it was accepted.
        raise AuthError(
            "The role assigned to this invite no longer exists. "
            "Please ask for a new invite.",
            status.HTTP_409_CONFLICT,
        )

    # Defensive checkpoint — send_invite() already prevents super_admin
    # invites from ever being created (see organization/invite_service.py
    # Guard 7a), but this is the last gate before a user account is
    # actually created and activated with that role. The super_admin
    # role must never be assignable through the invite flow.
    if role.name == RoleName.SUPER_ADMIN:
        raise AuthError(
            "This invite cannot be completed: the super_admin role "
            "cannot be assigned via invite registration.",
            status.HTTP_403_FORBIDDEN,
        )

    # ── Step 7b: role assignment (where the role applies) ──────────────────────
    await _create_role_assignment_if_needed(
        session,
        user=user,
        role=role,
        organization_id=invite.organization_id,
        branch_id=invite.branch_id,
        department_id=invite.invited_department_id,
    )

    # ── Step 7c: governance linking (e.g. branch_manager -> Branch.manager_id) ─
    await _apply_role_governance_linking(
        session,
        role=role,
        user=user,
        organization_id=invite.organization_id,
        branch_id=invite.branch_id,
        department_id=invite.invited_department_id,
    )

    # ── Step 8: wellbeing baseline ────────────────────────────────────────────
    if "entries" not in baseline:
        raise AuthError("Invalid baseline format.", status.HTTP_400_BAD_REQUEST)

    recorded = datetime.now(timezone.utc)
    for entry in baseline.get("entries", []):
        session.add(UserWellbeingBaseline(
            user_id=user.id,
            organization_id=user.organization_id,
            dimension=entry["dimension"],
            level=entry["level"],
            reason=entry.get("reason"),
            recorded_at=recorded,
        ))

    # ── Step 9: priorities ────────────────────────────────────────────────────
    for item in priorities:
        session.add(UserPriority(
            user_id=user.id,
            priority=item["priority"],
            rank=item["rank"],
        ))

    # ── Step 10: activate + mark invite accepted + audit ──────────────────────
    user.status = UserStatus.ACTIVE

    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = now
    invite.accepted_by_id = user.id

    await _log_audit(
        session,
        user=user,
        activity_type=ActivityType.JOINED_ORGANIZATION,
        reference_id=invite.organization_id,
        reference_type=ReferenceType.ORGANIZATION,
        metadata={
            "invited_by": str(invite.invited_by),
            "provider": OAuthProvider.PASSWORD,
            "department_id": invite.invited_department_id,
            "branch_id": str(invite.branch_id),
        },
    )
    await session.flush()

    # ── Step 11: issue tokens ─────────────────────────────────────────────────
    await session.refresh(user, ["role"])

    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)