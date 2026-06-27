"""
Example routes demonstrating the difference between public and
protected endpoints.

- GET /health        → public, no auth required, used for uptime checks
- GET /me            → protected, requires a valid bearer token

Use this file as the template for every future protected router.
Two flavors of protection are available:

- app.auth.dependencies.get_current_user_id — bare user id only,
  kept for simple cases that don't need role/org/branch.
- app.auth.scoping.get_current_user / require_role — the full
  CurrentUser (role, organization_id, branch_id) plus hierarchical
  role enforcement. Use this one for anything touching org- or
  branch-scoped data — which is most routes in this schema.
"""

from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user, require_role

router = APIRouter(tags=["general"])


@router.get("/health")
async def health_check():
    """Public endpoint — confirms the API is running. No auth required."""
    return {"status": "ok", "message": "Wellstaq API is running"}


@router.get("/me")
async def read_current_user(current_user: CurrentUser = Depends(get_current_user)):
    """
    Protected endpoint — requires a valid access token.
    Returns the resolved identity + scope (id, role, org, branch)
    straight from the token claims. Will return the full user
    profile once app/models/users.py and a database-backed lookup
    replace the TODO in app.auth.scoping.get_current_user.
    """
    return {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "organization_id": current_user.organization_id,
        "branch_id": current_user.branch_id,
    }


@router.get("/admin-only-example", dependencies=[Depends(require_role("branch_manager"))])
async def admin_only_example():
    """
    Demonstrates hierarchical role enforcement: employees get 403,
    branch_managers and overall_managers both get through, since
    overall_manager sits above branch_manager in the hierarchy.

    This is the pattern to copy for every future route that should
    be manager-only (creating departments, events, challenges, etc).
    """
    return {"message": "You have at least branch_manager access."}

