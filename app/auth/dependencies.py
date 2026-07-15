"""
app/auth/dependencies.py

FastAPI dependencies for protecting routes.

get_current_user is the single reusable dependency every protected
route uses. It:
  1. Decodes the JWT access token
  2. Loads the User row fresh from DB, eager-loading the role relationship
  3. Loads the user's directly-granted permissions from token_service
     (single shared implementation — no more duplicated query)
  4. Returns a CurrentUser dataclass

Loading fresh from DB on every request means role / org / permission
changes take effect immediately without waiting for token expiry.

Role vs Permission (independent in this model):
  role        — single label per user (role_id FK → Role.name).
                Used only for hierarchy checks via require_role /
                ROLE_HIERARCHY. Does NOT carry permissions.
  permissions — granted directly via UserPermission rows, scoped to
                organization_id. Loaded here via token_service.

Permission scope checking:
  require_permission()       — flat check: does the user hold this
                               permission anywhere (org-wide or any branch)?
                               Use for routes where scope doesn't matter.
  require_scoped_permission() — scope-aware check: does the user hold
                               this permission for a specific branch (or
                               org-wide, which inherits down)?
                               Use for routes that act on a specific branch
                               resource (e.g. suspend a user in Lagos branch).

  The two are intentionally separate:
    - require_permission()        → FastAPI dependency (used in route decorator)
    - require_scoped_permission() → async function (called inside route body,
                                    after the target branch_id is known)

Auth scheme:
  Uses HTTPBearer instead of OAuth2PasswordBearer. OAuth2PasswordBearer
  forces Swagger's Authorize modal to show a username/password form (OAuth2
  spec hardcodes "username"), which doesn't match our email-based login.
  HTTPBearer gives a single token field in the modal — paste the
  access_token from POST /auth/login and all protected endpoints work.
"""

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import TokenError, decode_token
from app.core.constants import TokenType, UserStatus
from app.core.database import get_db
from app.models import User, ClubMember, EventParticipant
from app.models.permission import Permission, UserPermission
from app.services.auth.token_service import load_user_permissions

http_bearer = HTTPBearer()

# Numeric hierarchy for require_role().
# Higher number = more privileged.
# NOTE: these are lowercase internal identifiers, not RoleName constants
# (which are title-cased DB display names like "Super Admin").
ROLE_HIERARCHY: dict[str, int] = {
    "employee": 0,
    "team_leader": 1,
    "branch_manager": 2,
    "super_admin": 3,
}


@dataclass(frozen=True)
class CurrentUser:
    """Resolved identity + scope for the requesting user."""
    user_id: str
    role: str | None           # single role name, or None if unassigned
    organization_id: str
    branch_id: str | None
    permissions: list[str] = field(default_factory=list)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def has_role(self, role_name: str) -> bool:
        return self.role == role_name


async def get_current_user(
    credentials=Depends(http_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """
    Decode the access token, load the user fresh from DB, resolve permissions.

    Raises:
      401 — token invalid / expired / wrong type
      403 — account not active, or no organization_id
    """
    token = credentials.credentials  # extracts raw token from Authorization header

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise credentials_exception from exc

    # Reject non-access tokens (refresh, 2fa_challenge, invite_verification, etc.)
    if payload.get("type") != TokenType.ACCESS:
        raise credentials_exception

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise credentials_exception

    # Load user fresh from DB. Eager-load role so we can read role.name
    # without a second query.
    # NOTE: User has no deleted_at column — hard-delete only.
    result = await db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active.",
        )

    if not user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with an organization.",
        )

    # Load permissions fresh from DB via the shared token_service implementation.
    # This replaces the previously inlined duplicate query.
    # Replace with a Redis cache hit once Redis is available.
    permissions = await load_user_permissions(db, user.id, user.organization_id)

    return CurrentUser(
        user_id=user.id,
        role=user.role.name if user.role else None,
        organization_id=user.organization_id,
        branch_id=user.branch_id,
        permissions=permissions,
    )


# ── Guard dependencies ─────────────────────────────────────────────────────────

def require_role(minimum_role: str):
    """
    Dependency factory enforcing a minimum role in the hierarchy.

    minimum_role must be a key in ROLE_HIERARCHY (lowercase).

    Usage:
        @router.post("/admin/action",
            dependencies=[Depends(require_role("branch_manager"))])
        async def admin_action(...): ...
    """
    if minimum_role not in ROLE_HIERARCHY:
        raise ValueError(
            f"Unknown role '{minimum_role}' — must be one of {list(ROLE_HIERARCHY)}"
        )

    async def dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if ROLE_HIERARCHY.get(current_user.role, -1) < ROLE_HIERARCHY[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires at least '{minimum_role}' role.",
            )
        return current_user

    return dependency


def require_permission(permission: str):
    """
    Flat permission check — does the user hold this permission anywhere
    (org-wide OR any branch)?

    Use this on routes where the action is not scoped to a specific branch
    — e.g. creating a department when the target branch_id is not yet
    known at the dependency layer (it arrives in the request body).

    For routes that act on a known branch resource, call
    require_scoped_permission() inside the route body instead, once the
    target branch_id is resolved.

    Usage:
        @router.post("/departments",
            dependencies=[Depends(require_permission("department.create"))])
        async def create_department(...): ...
    """
    async def dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if not current_user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: '{permission}'.",
            )
        return current_user

    return dependency


async def require_scoped_permission(
    db: AsyncSession,
    current_user: CurrentUser,
    permission: str,
    target_branch_id: str | None,
) -> None:
    """
    Scope-aware permission check. Call this inside a route body after the
    target branch_id is known — not as a decorator dependency.

    How scope is resolved:
      target_branch_id=None  (org-level action)
        → caller must hold the permission org-wide (branch_id IS NULL).
          A branch-scoped grant does NOT satisfy an org-level action.
          Example: creating an org-level department.

      target_branch_id=X  (branch-level action)
        → caller must hold the permission scoped to branch X
          OR org-wide (branch_id IS NULL), because org-wide grants
          inherit down to every branch automatically.
          Example: suspending a user in the Lagos branch.

    super_admin bypass:
      super_admin always passes regardless of their UserPermission rows,
      because they own the org and their role sits above the permission
      system. This mirrors the existing require_same_branch_or_org_level()
      behaviour.

    Raises:
      403 — permission not held at the required scope.

    Usage (inside a route):
        await require_scoped_permission(
            db=db,
            current_user=current_user,
            permission="member.suspend",
            target_branch_id=target_user.branch_id,
        )
    """
    # super_admin bypasses all scope checks — they own everything in the org.
    if current_user.role == "super_admin":
        return

    query = (
        select(UserPermission.id)          # only need existence, not the row
        .join(Permission, Permission.id == UserPermission.permission_id)
        .where(
            UserPermission.user_id == current_user.user_id,
            UserPermission.organization_id == current_user.organization_id,
            Permission.name == permission,
        )
    )

    if target_branch_id is not None:
        query = query.where(
            or_(
                UserPermission.branch_id.is_(None),
                UserPermission.branch_id == target_branch_id,
            )
        )
    else:
        query = query.where(UserPermission.branch_id.is_(None))

    query = query.limit(1)  # ← only ever need to know "at least one exists"

    result = await db.execute(query)
    if result.scalar_one_or_none() is None:
        scope_detail = (
            f"for branch '{target_branch_id}'"
            if target_branch_id
            else "at organization level"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: '{permission}' {scope_detail}.",
        )

def require_same_organization(
    target_organization_id: str,
    current_user: CurrentUser,
) -> None:
    """
    Call inside a route after loading a resource to enforce tenant isolation.
    Raises 403 if the user's org doesn't match the resource's org.
    """
    if current_user.organization_id != target_organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organization's data.",
        )


def require_same_branch_or_org_level(
    target_branch_id: str | None,
    current_user: CurrentUser,
) -> None:
    """
    Branch-scoped access guard. super_admin bypasses the check.
    All other roles are confined to their own branch_id.
    target_branch_id=None means an org-level resource — super_admin only.

    This is a ROLE + branch_id check, entirely separate from the
    permissions system.
    """
    if current_user.role == "super_admin":
        return

    if target_branch_id is None or current_user.branch_id != target_branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this branch's data.",
        )

async def require_club_membership(
    db: AsyncSession,
    current_user: CurrentUser,
    club_id: str,
) -> None:
    """
    Confirms current_user is a member of the given club. super_admin
    bypasses. A user can belong to multiple clubs (ClubMember has no
    per-user uniqueness constraint anymore — see Club model), so this
    checks for a specific (club_id, user_id) row, not "any club."

    Raises:
      403 — not a member of this club.

    Usage (inside a route or service function):
        await require_club_membership(db=db, current_user=current_user, club_id=club_id)
    """
    if current_user.role == "super_admin":
        return

    result = await db.execute(
        select(ClubMember.id).where(
            ClubMember.club_id == club_id,
            ClubMember.user_id == current_user.user_id,
        ).limit(1)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this club.",
        )


async def require_event_participation(
    db: AsyncSession,
    current_user: CurrentUser,
    event_id: str,
) -> None:
    """
    Confirms current_user is a participant of the given event. super_admin
    bypasses. Any participant status counts (invited/accepted/declined/
    pending) — tighten to status == 'accepted' only if acceptance should
    be required before someone can read/send in the event chat.

    ASSUMPTION: EventParticipant has event_id and user_id columns —
    inferred from events.py's get_event_participants/join_event usage.
    Adjust field names here if the actual model differs.

    Raises:
      403 — not a participant of this event.

    Usage (inside a route or service function):
        await require_event_participation(db=db, current_user=current_user, event_id=event_id)
    """
    if current_user.role == "super_admin":
        return

    result = await db.execute(
        select(EventParticipant.id).where(
            EventParticipant.event_id == event_id,
            EventParticipant.user_id == current_user.user_id,
        ).limit(1)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant of this event.",
        )