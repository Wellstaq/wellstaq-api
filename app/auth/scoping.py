"""
The single reusable scoping dependency.

With three roles (overall_manager | branch_manager | employee) and
organization_id/branch_id isolation touching nearly every table in
the schema, this check must live in exactly one place and be reused
everywhere — never copy-pasted per route.

This file currently resolves identity from the JWT only (user_id,
organization_id, branch_id, role — all embedded as token claims at
login time). Once app/models/users.py exists, swap the body of
get_current_user to load the full row from the database instead of
trusting claims alone, in case role/org/branch changes after a token
was issued (e.g. a user gets promoted or moved to a different
branch) — see the TODO inline below.

Role hierarchy (from highest to lowest):
    overall_manager > branch_manager > employee

Hierarchical means a check for "at least branch_manager" also passes
for an overall_manager — higher roles satisfy lower-role requirements.
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import oauth2_scheme
from app.auth.jwt import TokenError, decode_token

ROLE_HIERARCHY = {
    "employee": 0,
    "branch_manager": 1,
    "overall_manager": 2,
}


@dataclass(frozen=True)
class CurrentUser:
    """The resolved identity + scope for the requesting user."""

    user_id: str
    role: str
    organization_id: str | None
    branch_id: str | None


async def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    """
    Decode the access token and resolve the requester's identity and
    scope. Raises 401 for anything invalid, expired, or malformed.

    TODO once app/models/users.py + a DB session exist: load the user
    row from the database by id (rather than trusting the token's
    org/branch/role claims directly), so a role change or branch
    transfer takes effect immediately rather than only after the
    user's token naturally expires and they log in again.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise credentials_exception from exc

    if payload.get("type") != "access":
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    role = payload.get("role")
    if role not in ROLE_HIERARCHY:
        raise credentials_exception

    return CurrentUser(
        user_id=user_id,
        role=role,
        organization_id=payload.get("organization_id"),
        branch_id=payload.get("branch_id"),
    )


def require_role(minimum_role: str):
    """
    Dependency factory enforcing a hierarchical minimum role.

    Usage:
        @router.post("/departments", dependencies=[Depends(require_role("branch_manager"))])
        async def create_department(...): ...

    A branch_manager-only check also passes for overall_manager,
    since overall_manager sits above branch_manager in the hierarchy.
    An employee calling this would get 403.
    """
    if minimum_role not in ROLE_HIERARCHY:
        raise ValueError(f"Unknown role '{minimum_role}' — must be one of {list(ROLE_HIERARCHY)}")

    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if ROLE_HIERARCHY[current_user.role] < ROLE_HIERARCHY[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires at least '{minimum_role}' role.",
            )
        return current_user

    return dependency


def require_same_organization(target_organization_id: str, current_user: CurrentUser) -> None:
    """
    Call this inside a route/service after loading a target record
    (e.g. a department, club, or event) to enforce org isolation —
    a user from Org A must never read or modify Org B's data, even
    if they guess a valid UUID.

    overall_manager and branch_manager are still confined to their
    own organization_id; there's no cross-org "super admin" role in
    this schema, so this check applies to every role uniformly.
    """
    if current_user.organization_id != target_organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organization's data.",
        )


def require_same_branch_or_org_level(target_branch_id: str | None, current_user: CurrentUser) -> None:
    """
    For branch-scoped resources (departments/events/challenges that
    have a non-null branch_id): a branch_manager may only act within
    their own branch_id. An overall_manager bypasses this check
    entirely, since they operate across all branches in their org.

    target_branch_id of None means the resource is org-level (not
    tied to a specific branch) — only overall_manager may touch
    org-level resources; a branch_manager is confined to their branch.
    """
    if current_user.role == "overall_manager":
        return

    if target_branch_id is None or current_user.branch_id != target_branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this branch's data.",
        )
