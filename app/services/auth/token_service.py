"""
app/services/token_service.py

Owns everything that touches JWTs and UserDevice rows:
  - Building JWT claims from user + DB (shared with dependencies.py)
  - Issuing access + refresh token pairs
  - Refresh token rotation (invalidate old hash, issue new pair)
  - Revoking a device session

Alignment with app/auth/jwt.py
────────────────────────────────
jwt.py exposes:
  create_access_token(user_id, organization_id, branch_id, role, permissions)
  create_refresh_token(user_id)
  hash_refresh_token(raw_token)   ← used here instead of a local duplicate
  decode_token(token)

The previous draft called create_access_token(user_id, extra_claims=...,
expires_delta=...) which does not match jwt.py's actual signature.
This version calls it correctly.

Services never commit — the router owns the transaction boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,   # re-use jwt.py's implementation — no local duplicate
)
from app.models import Permission, Role, User, UserDevice, UserPermission

from dataclasses import dataclass


@dataclass(frozen=True)
class UserPermissionGrant:
    name: str
    branch_id: str | None
#  Shared permission loader 
# This is THE single implementation used by both:
#   • issue_tokens() / build_claims() (here, at login time)
#   • get_current_user() in dependencies.py (at every request)
#
async def load_user_permissions(
    session: AsyncSession, user_id: str, organization_id: str
) -> list[UserPermissionGrant]:
    """Load every permission directly granted to this user in this org,
    along with the branch it's scoped to (None = org-wide grant)."""
    result = await session.execute(
        select(Permission.name, UserPermission.branch_id)
        .join(UserPermission, UserPermission.permission_id == Permission.id)
        .where(
            UserPermission.user_id == user_id,
            UserPermission.organization_id == organization_id,
        )
        .distinct()
    )
    return [UserPermissionGrant(name=name, branch_id=branch_id) for name, branch_id in result.all()]
    
async def _load_role_name(session: AsyncSession, user: User) -> str | None:
    if not user.role_id:
        return None

    state = inspect(user)
    if "role" not in state.unloaded:
        # already eager-loaded — safe to access without triggering lazy-load I/O
        return user.role.name if user.role is not None else None

    result = await session.execute(select(Role.name).where(Role.id == user.role_id))
    return result.scalar_one_or_none()

async def build_claims(session: AsyncSession, user: User) -> dict:
    """
    Resolve the four extra claims that jwt.py's create_access_token expects.
    Returned as a plain dict — caller unpacks them as keyword args.
    """
    permissions = await load_user_permissions(session, user.id, user.organization_id)
    role_name = await _load_role_name(session, user)
    return {
        "organization_id": user.organization_id,
        "branch_id": user.branch_id,
        "role": role_name,
        "permissions": [{"name": p.name, "branch_id": p.branch_id} for p in permissions],
    }


# ── Token response shapes ──────────────────────────────────────────────────────

def token_pair_response(access_token: str, refresh_token: str) -> dict:
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


def access_only_response(access_token: str) -> dict:
    return {"access_token": access_token, "token_type": "bearer"}


def two_fa_challenge_response(challenge_token: str, method: str) -> dict:
    return {
        "requires_2fa": True,
        "two_fa_method": method,
        "two_fa_challenge_token": challenge_token,
    }


#  Device upsert 

async def _upsert_device(
    session: AsyncSession,
    user: User,
    refresh_hash: str,
    device_name: str | None,
    device_fingerprint: str | None,
    ip_address: str | None,
) -> None:
    """
    Upsert the UserDevice row for this session.

    If a non-revoked device with this fingerprint already exists, update it
    in-place (preserves device identity / history). Otherwise create a new row.

    Security note: device_fingerprint is client-supplied and can be spoofed.
    Treat it as a convenience hint for "same device" UX, not a security boundary.
    """
    device: UserDevice | None = None
    if device_fingerprint:
        result = await session.execute(
            select(UserDevice).where(
                UserDevice.user_id == user.id,
                UserDevice.device_fingerprint == device_fingerprint,
                UserDevice.revoked == False,  # noqa: E712
            )
        )
        device = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if device:
        device.last_seen_at = now
        device.refresh_token_hash = refresh_hash
        device.ip_address = ip_address
    else:
        session.add(UserDevice(
            user_id=user.id,
            organization_id=user.organization_id,
            device_name=device_name,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
            refresh_token_hash=refresh_hash,
            last_seen_at=now,
            revoked=False,
        ))


#  Issue (initial login / signup) 

async def issue_tokens(
    session: AsyncSession,
    user: User,
    device_name: str | None,
    device_fingerprint: str | None,
    ip_address: str | None,
) -> dict:
    """
    Issue a JWT pair, upsert the UserDevice row, return the raw token response.
    This is the FINAL step in every successful auth flow.

    Uses jwt.py's actual signatures:
      create_access_token(user_id, organization_id, branch_id, role, permissions)
      create_refresh_token(user_id)
      hash_refresh_token(raw_token)
    """
    claims = await build_claims(session, user)

    access_token = create_access_token(
        user_id=user.id,
        organization_id=claims["organization_id"],
        branch_id=claims["branch_id"],
        role=claims["role"],
        permissions=claims["permissions"],
    )
    raw_refresh = create_refresh_token(user.id)
    refresh_hash = hash_refresh_token(raw_refresh)

    await _upsert_device(session, user, refresh_hash, device_name, device_fingerprint, ip_address)
    await session.flush()

    return token_pair_response(access_token, raw_refresh)


# Rotate (on /auth/refresh) 

async def rotate_refresh_token(
    session: AsyncSession,
    user: User,
    old_device: UserDevice,
) -> dict:
    """
    Issue a new access + refresh pair and burn the old refresh token hash.

    Rotation means a leaked refresh token is single-use — the next legitimate
    use of the superseded token finds its hash gone and forces re-login.

    The device row is updated in-place to preserve device history / last_seen_at.
    """
    claims = await build_claims(session, user)

    new_access = create_access_token(
        user_id=user.id,
        organization_id=claims["organization_id"],
        branch_id=claims["branch_id"],
        role=claims["role"],
        permissions=claims["permissions"],
    )
    new_raw_refresh = create_refresh_token(user.id)
    new_hash = hash_refresh_token(new_raw_refresh)

    # Overwrite the old hash — old raw token is now unrecognisable in DB
    old_device.refresh_token_hash = new_hash
    old_device.last_seen_at = datetime.now(timezone.utc)
    await session.flush()

    return token_pair_response(new_access, new_raw_refresh)


# Revoke (on /auth/logout)

async def revoke_device(session: AsyncSession, device: UserDevice) -> None:
    """
    Mark a device session as revoked and burn its refresh token hash.
    The access token remains valid until natural expiry.

    TODO: add a Redis jti blocklist in create_access_token for immediate
    access token revocation if required by security policy.
    """
    device.revoked = True
    device.refresh_token_hash = None
    await session.flush()
