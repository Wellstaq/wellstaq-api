"""
tests/services/auth/test_session_service.py

Unit tests for app/services/auth/session_service.py.

Strategy:
  Build real JWTs via app.auth.jwt (not mocks) so token decode/expiry/type
  checks are exercised genuinely. UserDevice rows are inserted directly with
  hash_refresh_token() so refresh-token lookups match real behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_refresh_token, hash_refresh_token, _create_token
from app.core.constants import UserStatus
from app.core.datetime import ensure_utc
from app.models import User, UserDevice, UserSettings
from app.services.auth.session_service import get_me, logout, refresh_access_token
from app.services.errors import AuthError

from tests.conftest import make_org_with_owner, make_user_with_role


# ── Local helpers ────────────────────────────────────────────────────────────
async def _make_active_user(db_session: AsyncSession, *, email: str, **overrides) -> tuple:
    org, owner = await make_org_with_owner(db_session)
    overrides.setdefault("status", UserStatus.ACTIVE)
    overrides.setdefault("branch_id", owner.branch_id)
    user = await make_user_with_role(
        db_session, organization_id=org.id, email=email, **overrides
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(UserSettings(
        user_id=user.id,
        organization_id=user.organization_id,
    ))
    await db_session.flush()

    return org, user


def _add_device(
    db_session: AsyncSession,
    user: User,
    raw_refresh_token: str,
    *,
    revoked: bool = False,
    device_name: str | None = "TestDevice",
    device_fingerprint: str | None = "test-fp",
) -> UserDevice:
    device = UserDevice(
        user_id=user.id,
        organization_id=user.organization_id,
        device_name=device_name,
        device_fingerprint=device_fingerprint,
        ip_address="127.0.0.1",
        refresh_token_hash=hash_refresh_token(raw_refresh_token),
        last_seen_at=datetime.now(timezone.utc),
        revoked=revoked,
    )
    db_session.add(device)
    return device


def _expired_refresh_token(user_id: str) -> str:
    """A refresh token with exp already in the past."""
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(days=-1),
        token_type="refresh",
    )


def _access_token_as_refresh(user_id: str) -> str:
    """A validly-signed token, but type='access' — should be rejected by
    refresh_access_token's type check."""
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=15),
        token_type="access",
    )


# ── refresh_access_token ───────────────────────────────────────────────────

class TestRefreshAccessToken:

    @pytest.mark.asyncio
    async def test_valid_refresh_rotates_and_returns_new_pair(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="refresh@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        device = _add_device(db_session, user, raw_refresh)
        await db_session.flush()
        old_hash = device.refresh_token_hash

        result = await refresh_access_token(db_session, raw_refresh)

        assert "access_token" in result
        assert "refresh_token" in result
        assert result["refresh_token"] != raw_refresh

        await db_session.refresh(device)
        assert device.refresh_token_hash != old_hash
        assert device.refresh_token_hash == hash_refresh_token(result["refresh_token"])

    @pytest.mark.asyncio
    async def test_old_refresh_token_unusable_after_rotation(self, db_session: AsyncSession):
        """Single-use enforcement: rotating burns the old hash immediately,
        so replaying the original raw token must fail."""
        org, user = await _make_active_user(db_session, email="singleuse@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        _add_device(db_session, user, raw_refresh)
        await db_session.flush()

        await refresh_access_token(db_session, raw_refresh)

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, raw_refresh)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_token_raises_401(self, db_session: AsyncSession):
        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, "not-a-real-jwt")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="expired@gmail.com")
        expired = _expired_refresh_token(user.id)
        _add_device(db_session, user, expired)
        await db_session.flush()

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, expired)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_access_token_type_rejected(self, db_session: AsyncSession):
        """An otherwise-valid, correctly-signed token must be rejected if
        its type claim isn't 'refresh' — prevents using a short-lived access
        token to mint new tokens."""
        org, user = await _make_active_user(db_session, email="wrongtype@gmail.com")
        access_as_refresh = _access_token_as_refresh(user.id)

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, access_as_refresh)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unrecognised_device_raises_401(self, db_session: AsyncSession):
        """Token is valid and well-formed, but no UserDevice row matches its
        hash (e.g. device was deleted, or hash mismatch)."""
        org, user = await _make_active_user(db_session, email="nodevice@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        # no _add_device call — nothing in DB for this token

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, raw_refresh)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_device_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="revokeddevice@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        _add_device(db_session, user, raw_refresh, revoked=True)
        await db_session.flush()

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, raw_refresh)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_suspended_user_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(
            db_session, email="suspended@gmail.com", status=UserStatus.SUSPENDED
        )
        raw_refresh = create_refresh_token(user.id)
        _add_device(db_session, user, raw_refresh)
        await db_session.flush()

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, raw_refresh)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_inactive_user_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(
            db_session, email="inactiveuser@gmail.com", status=UserStatus.INACTIVE
        )
        raw_refresh = create_refresh_token(user.id)
        _add_device(db_session, user, raw_refresh)
        await db_session.flush()

        with pytest.raises(AuthError) as exc_info:
            await refresh_access_token(db_session, raw_refresh)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_pending_user_is_allowed(self, db_session: AsyncSession):
        """Unlike oauth_login, refresh_access_token only blocks SUSPENDED and
        INACTIVE — PENDING is not excluded, so a mid-onboarding user with an
        existing session can still refresh."""
        org, user = await _make_active_user(
            db_session, email="pendingrefresh@gmail.com", status=UserStatus.PENDING
        )
        raw_refresh = create_refresh_token(user.id)
        _add_device(db_session, user, raw_refresh)
        await db_session.flush()

        result = await refresh_access_token(db_session, raw_refresh)
        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_device_last_seen_at_updated_on_rotation(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="lastseen@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        device = _add_device(db_session, user, raw_refresh)
        await db_session.flush()
        old_last_seen = device.last_seen_at

        await refresh_access_token(db_session, raw_refresh)
        await db_session.refresh(device)

        assert ensure_utc(device.last_seen_at) >= old_last_seen


# ── logout ───────────────────────────────────────────────────────────────────

class TestLogout:

    @pytest.mark.asyncio
    async def test_valid_token_revokes_device(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="logout@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        device = _add_device(db_session, user, raw_refresh)
        await db_session.flush()

        await logout(db_session, raw_refresh)
        await db_session.refresh(device)

        assert device.revoked is True
        assert device.refresh_token_hash is None

    @pytest.mark.asyncio
    async def test_double_logout_is_idempotent(self, db_session: AsyncSession):
        """Second logout call with the same (now-stale) token must not raise —
        it should just no-op since the device is already revoked / hash gone."""
        org, user = await _make_active_user(db_session, email="doublelogout@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        device = _add_device(db_session, user, raw_refresh)
        await db_session.flush()

        await logout(db_session, raw_refresh)
        # second call: device.refresh_token_hash is now None, so the lookup
        # by token_hash won't find this device — should still return cleanly
        await logout(db_session, raw_refresh)

        await db_session.refresh(device)
        assert device.revoked is True

    @pytest.mark.asyncio
    async def test_malformed_token_returns_silently(self, db_session: AsyncSession):
        # Should not raise — logout is intentionally lenient
        result = await logout(db_session, "not-a-real-jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_silently(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="expiredlogout@gmail.com")
        expired = _expired_refresh_token(user.id)
        device = _add_device(db_session, user, expired)
        await db_session.flush()

        result = await logout(db_session, expired)

        assert result is None
        await db_session.refresh(device)
        assert device.revoked is False  # never reached the revoke step

    @pytest.mark.asyncio
    async def test_unrecognised_device_returns_silently(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="nodevicelogout@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        # no device row added

        result = await logout(db_session, raw_refresh)
        assert result is None

    @pytest.mark.asyncio
    async def test_already_revoked_device_not_touched_again(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="alreadyrevoked@gmail.com")
        raw_refresh = create_refresh_token(user.id)
        device = _add_device(db_session, user, raw_refresh, revoked=True)
        await db_session.flush()

        # device.revoked is already True, so logout's `not device.revoked`
        # guard should skip calling revoke_device again
        await logout(db_session, raw_refresh)

        await db_session.refresh(device)
        assert device.revoked is True


# ── get_me ───────────────────────────────────────────────────────────────────
class TestGetMe:

    @pytest.mark.asyncio
    async def test_returns_user_with_settings_loaded(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="getme@gmail.com")

        result, department_id = await get_me(db_session, user.id)

        assert result.id == user.id
        assert result.email == "getme@gmail.com"
        assert result.settings is not None