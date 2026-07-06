"""
tests/services/auth/test_two_factor_service.py

Unit tests for app/services/two_factor_service.py.

Strategy:
  - TOTP tests use real generate_totp_secret/verify_totp (stdlib HMAC, no I/O).
  - Email-path OTP tests monkeypatch otp_service._send_email_otp to avoid
    hitting a real provider, but otherwise exercise the real OTP hash/
    store/attempt-count logic in otp_service.
  - SMS is permanently unimplemented in otp_service (always raises 501) —
    tested as a deterministic failure path, no mocking required.
"""

from __future__ import annotations

import base64
import hmac
import struct
import time
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import _create_token
from app.auth.security import hash_password
from app.core.constants import TokenType, TwoFaMethod, UserStatus
from app.models import User, UserSettings
from app.services.auth._helpers import _create_user_settings
from app.services.auth.otp_service import request_otp
from app.services.auth.two_factor_service import (
    confirm_two_fa_setup,
    disable_two_fa,
    generate_totp_secret,
    initiate_two_fa_setup,
    totp_uri,
    verify_totp,
    verify_two_fa_challenge,
)

from tests.conftest import make_org_with_owner, make_user_with_role


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _make_active_user(
    db_session: AsyncSession,
    *,
    email: str,
    password: str | None = None,
    **overrides,
) -> tuple:
    org, owner = await make_org_with_owner(db_session)
    overrides.setdefault("status", UserStatus.ACTIVE)
    overrides.setdefault("branch_id", owner.branch_id)   
    if password is not None:
        overrides["hashed_password"] = hash_password(password)
    user = await make_user_with_role(
        db_session, organization_id=org.id, email=email, **overrides
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(_create_user_settings(user))
    await db_session.flush()

    await db_session.refresh(user, attribute_names=["settings"])
    return org, user


def _current_totp_code(secret: str) -> str:
    """Derive the current valid 6-digit TOTP code for a given base32 secret."""
    key = base64.b32decode(secret.upper())
    counter = int(time.time()) // 30
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, "sha1").digest()
    offset = h[-1] & 0x0F
    code_int = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % 1_000_000).zfill(6)


def _challenge_token(
    user_id: str,
    method: str,
    *,
    expires_delta: timedelta | None = None,
    device_name: str | None = "TestDevice",
    device_fingerprint: str | None = "test-fp",
    ip_address: str | None = "127.0.0.1",
) -> str:
    """Build a real signed 2FA challenge token for test use."""
    return _create_token(
        subject=user_id,
        expires_delta=expires_delta or timedelta(minutes=5),
        token_type="access",
        extra_claims={
            "type": TokenType.TWO_FA_CHALLENGE,
            "two_fa_method": method,
            "device_name": device_name,
            "device_fingerprint": device_fingerprint,
            "ip_address": ip_address,
        },
    )


async def _enable_totp(db_session: AsyncSession, user: User) -> str:
    """
    Activate TOTP on a user's settings row, returning the plain secret.

    Queries UserSettings explicitly rather than accessing user.settings —
    the latter triggers a lazy load which raises MissingGreenlet in an
    async context, even if the relationship was previously loaded.
    """
    result = await db_session.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    settings_row = result.scalar_one()
    secret = generate_totp_secret()
    settings_row.two_fa_enabled = True
    settings_row.two_fa_method = TwoFaMethod.TOTP
    settings_row.two_fa_secret = secret
    await db_session.flush()
    return secret


@pytest.fixture
def patch_email_otp(monkeypatch):
    """
    Stub out real email delivery — captures the raw OTP code per email
    address so tests can complete verify steps without a real inbox.
    Patches app.services.auth.otp_service (the actual module location).
    """
    sent: dict[str, str] = {}

    async def _fake_send(email: str, code: str, purpose: str) -> None:
        sent[email] = code

    monkeypatch.setattr(
        "app.services.auth.otp_service._send_email_otp", _fake_send
    )
    return sent


# ── TOTP primitives ──────────────────────────────────────────────────────────

class TestTotpPrimitives:

    def test_generate_secret_is_valid_base32_of_correct_length(self):
        secret = generate_totp_secret()
        # 20 random bytes base32-encoded = 32 characters
        decoded = base64.b32decode(secret.upper())
        assert len(decoded) == 20

    def test_totp_uri_format(self):
        secret = generate_totp_secret()
        uri = totp_uri(secret, "user@example.com")
        assert uri.startswith("otpauth://totp/")
        assert f"secret={secret}" in uri
        assert "issuer=" in uri

    def test_verify_totp_accepts_current_window_code(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, _current_totp_code(secret)) is True

    def test_verify_totp_rejects_wrong_code(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, "000000") is False

    def test_verify_totp_rejects_malformed_secret(self):
        assert verify_totp("not-valid-base32!!!", "123456") is False

    def test_verify_totp_rejects_empty_code(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, "") is False


# ── initiate_two_fa_setup ────────────────────────────────────────────────────

class TestInitiateTwoFaSetup:

    @pytest.mark.asyncio
    async def test_totp_stores_pending_secret_and_returns_uri(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(db_session, email="totpinit@example.com")

        result = await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.TOTP)

        assert result["method"] == TwoFaMethod.TOTP
        assert result["totp_secret"]
        assert result["totp_uri"].startswith("otpauth://totp/")
        assert result["totp_secret"] in result["totp_uri"]

        # Re-query to avoid lazy-load after flush
        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_secret == f"pending:{result['totp_secret']}"
        assert settings.two_fa_enabled is False
        assert settings.two_fa_method is None  # not set until confirm

    @pytest.mark.asyncio
    async def test_email_stores_pending_marker_and_sends_otp(
        self, db_session: AsyncSession, patch_email_otp: dict
    ):
        org, user = await _make_active_user(db_session, email="emailinit@example.com")

        result = await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.EMAIL)

        assert result["method"] == TwoFaMethod.EMAIL
        assert result["totp_secret"] is None
        assert result["totp_uri"] is None
        assert user.email in patch_email_otp  # OTP was "sent"

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_secret == "pending:email"

    @pytest.mark.asyncio
    async def test_sms_raises_501_not_implemented(self, db_session: AsyncSession):
        """SMS delivery is not integrated — otp_service rejects it before
        any OtpCode row is created."""
        org, user = await _make_active_user(db_session, email="smsinit@example.com")

        with pytest.raises(Exception) as exc_info:
            await initiate_two_fa_setup(
                db_session, user.id, TwoFaMethod.SMS, phone_number="+15555550100"
            )
        assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_sms_without_phone_number_raises_422(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="smsnophone@example.com")

        with pytest.raises(Exception) as exc_info:
            await initiate_two_fa_setup(
                db_session, user.id, TwoFaMethod.SMS, phone_number=None
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_method_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="badmethod@example.com")

        with pytest.raises(Exception) as exc_info:
            await initiate_two_fa_setup(db_session, user.id, "carrier_pigeon")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_already_enabled_raises_409(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="alreadyon@example.com")
        await _enable_totp(db_session, user)

        with pytest.raises(Exception) as exc_info:
            await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.TOTP)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_unknown_user_raises_404(self, db_session: AsyncSession):
        with pytest.raises(Exception) as exc_info:
            await initiate_two_fa_setup(
                db_session, "00000000-0000-0000-0000-000000000000", TwoFaMethod.TOTP
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_settings_row_raises_500(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)   # ← change this line
        bare_user = User(
            first_name="No",
            last_name="Settings",
            email="nosettings@example.com",
            organization_id=org.id,
            branch_id=owner.branch_id,                 
            status=UserStatus.ACTIVE,
        )
        db_session.add(bare_user)
        await db_session.flush()

        with pytest.raises(Exception) as exc_info:
            await initiate_two_fa_setup(db_session, bare_user.id, TwoFaMethod.TOTP)
        assert exc_info.value.status_code == 500


# ── confirm_two_fa_setup ──────────────────────────────────────────────────────

class TestConfirmTwoFaSetup:

    @pytest.mark.asyncio
    async def test_totp_confirm_activates_and_strips_pending_prefix(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(db_session, email="totpconfirm@example.com")
        result = await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.TOTP)
        secret = result["totp_secret"]

        await confirm_two_fa_setup(db_session, user.id, _current_totp_code(secret))

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_enabled is True
        assert settings.two_fa_method == TwoFaMethod.TOTP
        assert settings.two_fa_secret == secret  # pending: prefix stripped

    @pytest.mark.asyncio
    async def test_totp_wrong_code_raises_401_and_stays_disabled(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(db_session, email="totpwrong@example.com")
        await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.TOTP)

        with pytest.raises(Exception) as exc_info:
            await confirm_two_fa_setup(db_session, user.id, "000000")
        assert exc_info.value.status_code == 401

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_enabled is False

    @pytest.mark.asyncio
    async def test_email_confirm_activates_2fa(
        self, db_session: AsyncSession, patch_email_otp: dict
    ):
        org, user = await _make_active_user(db_session, email="emailconfirm@example.com")
        await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.EMAIL)
        sent_code = patch_email_otp[user.email]

        await confirm_two_fa_setup(db_session, user.id, sent_code)

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_enabled is True
        assert settings.two_fa_method == TwoFaMethod.EMAIL
        assert settings.two_fa_secret == None  # pending: prefix stripped

    @pytest.mark.asyncio
    async def test_email_wrong_code_raises_error(
        self, db_session: AsyncSession, patch_email_otp: dict
    ):
        org, user = await _make_active_user(db_session, email="emailwrong@example.com")
        await initiate_two_fa_setup(db_session, user.id, TwoFaMethod.EMAIL)

        with pytest.raises(Exception):
            await confirm_two_fa_setup(db_session, user.id, "000000")

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_enabled is False

    @pytest.mark.asyncio
    async def test_confirm_without_pending_setup_raises_400(
        self, db_session: AsyncSession
    ):
        """No initiate call made — two_fa_secret is None, missing the pending: prefix."""
        org, user = await _make_active_user(db_session, email="nopending@example.com")

        with pytest.raises(Exception) as exc_info:
            await confirm_two_fa_setup(db_session, user.id, "123456")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_confirm_when_already_enabled_raises_409(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(db_session, email="confirmenabled@example.com")
        await _enable_totp(db_session, user)

        with pytest.raises(Exception) as exc_info:
            await confirm_two_fa_setup(db_session, user.id, "123456")
        assert exc_info.value.status_code == 409


# ── verify_two_fa_challenge ───────────────────────────────────────────────────

class TestVerifyTwoFaChallenge:

    @pytest.mark.asyncio
    async def test_valid_totp_challenge_issues_token_pair(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(db_session, email="challenge@example.com")
        secret = await _enable_totp(db_session, user)
        token = _challenge_token(user.id, TwoFaMethod.TOTP)

        result = await verify_two_fa_challenge(
            db_session, token, _current_totp_code(secret)
        )

        assert "access_token" in result
        assert "refresh_token" in result
        assert result["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_wrong_totp_code_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="challengewrong@example.com")
        await _enable_totp(db_session, user)
        token = _challenge_token(user.id, TwoFaMethod.TOTP)

        with pytest.raises(Exception) as exc_info:
            await verify_two_fa_challenge(db_session, token, "000000")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_challenge_token_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="challengeexpired@example.com")
        secret = await _enable_totp(db_session, user)
        token = _challenge_token(
            user.id, TwoFaMethod.TOTP, expires_delta=timedelta(minutes=-1)
        )

        with pytest.raises(Exception) as exc_info:
            await verify_two_fa_challenge(
                db_session, token, _current_totp_code(secret)
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_token_raises_401(self, db_session: AsyncSession):
        with pytest.raises(Exception) as exc_info:
            await verify_two_fa_challenge(db_session, "not-a-real-jwt", "123456")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_token_type_raises_401(self, db_session: AsyncSession):
        """A refresh token is validly signed but type != two_fa_challenge —
        must be rejected before any user lookup."""
        org, user = await _make_active_user(db_session, email="wrongtype@example.com")
        await _enable_totp(db_session, user)
        wrong_type = _create_token(
            subject=user.id,
            expires_delta=timedelta(minutes=5),
            token_type="refresh",
        )

        with pytest.raises(Exception) as exc_info:
            await verify_two_fa_challenge(db_session, wrong_type, "123456")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_2fa_not_enabled_raises_400(self, db_session: AsyncSession):
        """User exists and has settings but hasn't enabled 2FA — settings
        guard must block it even with a valid challenge token."""
        org, user = await _make_active_user(db_session, email="notenabled@example.com")
        token = _challenge_token(user.id, TwoFaMethod.TOTP)

        with pytest.raises(Exception) as exc_info:
            await verify_two_fa_challenge(db_session, token, "123456")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_email_challenge_verifies_via_otp_store(
        self, db_session: AsyncSession, patch_email_otp: dict
    ):
        org, user = await _make_active_user(db_session, email="emailchallenge@example.com")
        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        settings.two_fa_enabled = True
        settings.two_fa_method = TwoFaMethod.EMAIL
        await db_session.flush()

        # Simulate the OTP that would have been sent at login time
        await request_otp(db_session, user.email, purpose="2fa_email")
        sent_code = patch_email_otp[user.email]

        token = _challenge_token(user.id, TwoFaMethod.EMAIL)
        result = await verify_two_fa_challenge(db_session, token, sent_code)

        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_unknown_user_in_challenge_token_raises_404(
        self, db_session: AsyncSession
    ):
        token = _challenge_token(
            "00000000-0000-0000-0000-000000000000", TwoFaMethod.TOTP
        )

        with pytest.raises(Exception) as exc_info:
            await verify_two_fa_challenge(db_session, token, "123456")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_device_fingerprint_from_challenge_token_wins_over_caller(
        self, db_session: AsyncSession
    ):
        """
        Device context is embedded in the challenge token at login time.
        A caller passing a different device_fingerprint at verify time must
        not override it — the token's embedded value wins.
        This closes the vector where a MITM swaps device context between
        the challenge and verify steps.
        """
        from app.models import UserDevice

        org, user = await _make_active_user(db_session, email="devicewins@example.com")
        secret = await _enable_totp(db_session, user)
        token = _challenge_token(
            user.id, TwoFaMethod.TOTP, device_fingerprint="original-fp"
        )

        await verify_two_fa_challenge(
            db_session,
            token,
            _current_totp_code(secret),
            device_fingerprint="spoofed-fp",
        )

        device_result = await db_session.execute(
            select(UserDevice).where(UserDevice.user_id == user.id)
        )
        device = device_result.scalar_one()
        assert device.device_fingerprint == "original-fp"


# ── disable_two_fa ────────────────────────────────────────────────────────────

class TestDisableTwoFa:

    @pytest.mark.asyncio
    async def test_correct_password_disables_and_clears_2fa_fields(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(
            db_session, email="disable@example.com", password="correct-horse"
        )
        await _enable_totp(db_session, user)

        await disable_two_fa(db_session, user.id, "correct-horse")

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_enabled is False
        assert settings.two_fa_method is None
        assert settings.two_fa_secret is None

    @pytest.mark.asyncio
    async def test_wrong_password_raises_401_and_leaves_2fa_intact(
        self, db_session: AsyncSession
    ):
        org, user = await _make_active_user(
            db_session, email="disablewrong@example.com", password="correct-horse"
        )
        await _enable_totp(db_session, user)

        with pytest.raises(Exception) as exc_info:
            await disable_two_fa(db_session, user.id, "wrong-password")
        assert exc_info.value.status_code == 401

        settings = (await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )).scalar_one()
        assert settings.two_fa_enabled is True  # unchanged on failure

    @pytest.mark.asyncio
    async def test_oauth_only_user_raises_400(self, db_session: AsyncSession):
        """
        OAuth-only users have hashed_password=None — disable_two_fa must
        reject them before bcrypt is attempted.
        hashed_password=None is passed explicitly to override the factory
        default of "$2b$12$fakehash".
        """
        org, user = await _make_active_user(
            db_session, email="oauthonly@example.com", hashed_password=None
        )
        await _enable_totp(db_session, user)
        assert user.hashed_password is None  # confirm override took effect

        with pytest.raises(Exception) as exc_info:
            await disable_two_fa(db_session, user.id, "anything")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_not_enabled_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(
            db_session, email="notenableddisable@example.com", password="correct-horse"
        )
        # 2FA is off by default — intentionally no _enable_totp call

        with pytest.raises(Exception) as exc_info:
            await disable_two_fa(db_session, user.id, "correct-horse")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_user_raises_404(self, db_session: AsyncSession):
        with pytest.raises(Exception) as exc_info:
            await disable_two_fa(
                db_session, "00000000-0000-0000-0000-000000000000", "password"
            )
        assert exc_info.value.status_code == 404