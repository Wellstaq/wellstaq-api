"""
Service tests for app/services/platform_admin/auth_service.py.

TOTP tests use real generate_totp_secret/verify_totp (stdlib HMAC, no
I/O) — same approach as tests/services/auth/test_two_factor_service.py.
Email-path 2FA and password reset exercise the real otp_service/Redis
logic, monkeypatching only the outbound email send.
"""

from __future__ import annotations

import base64
import hmac
import struct
import time

import pytest
from fastapi import HTTPException

from app.auth.security import hash_password
from app.services.auth.two_factor_service import generate_totp_secret
from app.services.platform_admin import auth_service
from tests.conftest import make_admin_user


def _current_totp_code(secret: str) -> str:
    key = base64.b32decode(secret.upper())
    counter = int(time.time()) // 30
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, "sha1").digest()
    offset = h[-1] & 0x0F
    code_int = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % 1_000_000).zfill(6)


async def _persisted_admin(db_session, **overrides):
    overrides.setdefault("hashed_password", hash_password("correct-horse-battery-staple"))
    admin = make_admin_user(**overrides)
    db_session.add(admin)
    await db_session.flush()
    return admin


@pytest.mark.asyncio
async def test_login_succeeds_with_correct_password(db_session):
    await _persisted_admin(db_session, email="owner@wellstaq.test")
    result = await auth_service.login(session=db_session, email="owner@wellstaq.test", password="correct-horse-battery-staple")
    assert "access_token" in result
    assert "refresh_token" in result


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(db_session):
    await _persisted_admin(db_session, email="owner@wellstaq.test")
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(session=db_session, email="owner@wellstaq.test", password="wrong-password")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_unknown_email_with_same_error_as_wrong_password(db_session):
    """Anti-enumeration: both paths must 401 with the same message shape."""
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(session=db_session, email="nobody@wellstaq.test", password="whatever")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_blocks_disabled_admin(db_session):
    await _persisted_admin(db_session, email="disabled@wellstaq.test", status="disabled")
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(session=db_session, email="disabled@wellstaq.test", password="correct-horse-battery-staple")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_login_blocks_invited_admin(db_session):
    await _persisted_admin(db_session, email="invited@wellstaq.test", status="invited")
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(session=db_session, email="invited@wellstaq.test", password="correct-horse-battery-staple")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_login_with_totp_enabled_returns_challenge_not_tokens(db_session):
    secret = generate_totp_secret()
    await _persisted_admin(db_session, email="2fa@wellstaq.test", two_fa_enabled=True, two_fa_method="totp", two_fa_secret=secret)
    result = await auth_service.login(session=db_session, email="2fa@wellstaq.test", password="correct-horse-battery-staple")
    assert result["two_fa_required"] is True
    assert result["two_fa_method"] == "totp"
    assert "challenge_token" not in result  # the key is two_fa_challenge_token
    assert "two_fa_challenge_token" in result


@pytest.mark.asyncio
async def test_verify_2fa_with_valid_totp_code_issues_tokens(db_session):
    secret = generate_totp_secret()
    await _persisted_admin(db_session, email="2fa@wellstaq.test", two_fa_enabled=True, two_fa_method="totp", two_fa_secret=secret)
    challenge = await auth_service.login(session=db_session, email="2fa@wellstaq.test", password="correct-horse-battery-staple")

    result = await auth_service.verify_2fa(
        session=db_session, challenge_token=challenge["two_fa_challenge_token"], code=_current_totp_code(secret),
    )
    assert "access_token" in result


@pytest.mark.asyncio
async def test_verify_2fa_with_wrong_code_fails(db_session):
    secret = generate_totp_secret()
    await _persisted_admin(db_session, email="2fa@wellstaq.test", two_fa_enabled=True, two_fa_method="totp", two_fa_secret=secret)
    challenge = await auth_service.login(session=db_session, email="2fa@wellstaq.test", password="correct-horse-battery-staple")

    with pytest.raises(HTTPException) as exc:
        await auth_service.verify_2fa(session=db_session, challenge_token=challenge["two_fa_challenge_token"], code="000000")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_old_one_stops_working(db_session):
    admin = await _persisted_admin(db_session, email="owner@wellstaq.test")
    first = await auth_service.login(session=db_session, email="owner@wellstaq.test", password="correct-horse-battery-staple")

    second = await auth_service.refresh(session=db_session, refresh_token=first["refresh_token"])
    assert second["access_token"] != first["access_token"]

    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(session=db_session, refresh_token=first["refresh_token"])
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_session(db_session):
    admin = await _persisted_admin(db_session, email="owner@wellstaq.test")
    tokens = await auth_service.login(session=db_session, email="owner@wellstaq.test", password="correct-horse-battery-staple")

    from app.auth.jwt import decode_token
    session_id = decode_token(tokens["access_token"])["sid"]
    await auth_service.logout(session=db_session, session_id=session_id)

    with pytest.raises(HTTPException):
        await auth_service.refresh(session=db_session, refresh_token=tokens["refresh_token"])


@pytest.mark.asyncio
async def test_request_password_reset_is_silent_no_op_for_unknown_email(db_session):
    # Must not raise — anti-enumeration, same as the org-user equivalent.
    await auth_service.request_password_reset(session=db_session, email="nobody@wellstaq.test")


@pytest.mark.asyncio
async def test_reset_password_activates_an_invited_admin(db_session, monkeypatch):
    """Also covers the invite flow (admin_team_service.invite_admin reuses
    this exact mechanism — see its docstring) since the reset-link token
    IS the invited admin's way to set their first password."""
    admin = await _persisted_admin(db_session, email="invited@wellstaq.test", status="invited", hashed_password=None)

    captured = {}
    async def _capture(*, email, reset_token):
        captured["token"] = reset_token
    monkeypatch.setattr(auth_service, "_send_reset_email", _capture)
    await auth_service.request_password_reset(session=db_session, email="invited@wellstaq.test")

    await auth_service.reset_password(session=db_session, reset_token=captured["token"], new_password="BrandNewPassword123!")
    await db_session.refresh(admin)
    assert admin.status == "active"
    assert admin.hashed_password is not None


@pytest.mark.asyncio
async def test_reset_password_rejects_reused_token(db_session, monkeypatch):
    await _persisted_admin(db_session, email="owner@wellstaq.test")
    captured = {}
    async def _capture(*, email, reset_token):
        captured["token"] = reset_token
    monkeypatch.setattr(auth_service, "_send_reset_email", _capture)
    await auth_service.request_password_reset(session=db_session, email="owner@wellstaq.test")

    await auth_service.reset_password(session=db_session, reset_token=captured["token"], new_password="FirstNewPassword123!")
    with pytest.raises(HTTPException) as exc:
        await auth_service.reset_password(session=db_session, reset_token=captured["token"], new_password="SecondNewPassword123!")
    assert exc.value.status_code == 400
