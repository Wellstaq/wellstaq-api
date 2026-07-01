"""
tests/services/auth/test_otp_service.py

Unit tests for app/services/auth/otp_service.py.

Strategy:
  - Email delivery (_send_email_otp) is mocked on every test — we never
    hit SendGrid in tests, and the OtpCode storage is SQLite in-memory.
  - When Redis replaces OtpCode, only the fixtures that insert/read
    OtpCode rows need to change. Every assertion about business logic
    (attempt counting, locking, expiry, JWT shape) remains valid.
  - _make_code() and _hash_code() are pure functions — tested directly,
    no DB or mocking needed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import OTP_LENGTH, OTP_MAX_ATTEMPTS, OTP_TTL_MINUTES, TokenType
from app.models.otp_code import OtpCode
from app.services.auth.otp_service import (
    _hash_code,
    _make_code,
    request_otp,
    verify_otp,
)
from app.services.errors import OtpError
from app.auth.jwt import decode_token
from tests.conftest import make_org_with_owner


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_naive() -> datetime:
    """
    Timezone-naive UTC datetime for comparisons against values read from
    SQLite, which strips timezone info on storage. PostgreSQL preserves it
    so _now() works there directly — this helper is test-only.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_otp_record(
    session: AsyncSession,
    email: str,
    *,
    raw_code: str = "123456",
    used: bool = False,
    expired: bool = False,
    attempt_count: int = 0,
) -> OtpCode:
    """
    Insert a pre-built OtpCode row directly — bypasses request_otp() so
    tests can control the exact code, expiry, and state without mocking
    the email sender.
    """
    expires_at = (
        _now() - timedelta(minutes=1)   # already expired
        if expired
        else _now() + timedelta(minutes=OTP_TTL_MINUTES)
    )
    record = OtpCode(
        email=email.lower().strip(),
        code_hash=_hash_code(raw_code),
        expires_at=expires_at,
        used=used,
        attempt_count=attempt_count,
    )
    session.add(record)
    return record


# ── _make_code() ───────────────────────────────────────────────────────────────

class TestMakeCode:
    def test_correct_length(self):
        for _ in range(50):
            code = _make_code()
            assert len(code) == OTP_LENGTH, (
                f"Expected {OTP_LENGTH} digits, got {len(code)}: {code!r}"
            )

    def test_only_numeric_characters(self):
        for _ in range(50):
            code = _make_code()
            assert code.isdigit(), f"Non-numeric character in code: {code!r}"

    def test_leading_zeros_preserved(self):
        """
        zfill() must preserve leading zeros. If secrets.randbelow returns
        a value like 42891, the code must be "042891" not "42891".
        We can't force a leading zero from the random source, but we can
        verify the contract holds for any generated code.
        """
        # Generate many codes — statistically at least a few will start
        # with zero if the implementation is correct.
        codes = [_make_code() for _ in range(500)]
        assert all(len(c) == OTP_LENGTH for c in codes), (
            "Some codes were not zero-padded to the correct length."
        )


# ── _hash_code() ───────────────────────────────────────────────────────────────

class TestHashCode:
    def test_deterministic(self):
        assert _hash_code("123456") == _hash_code("123456")

    def test_different_inputs_produce_different_hashes(self):
        assert _hash_code("123456") != _hash_code("654321")

    def test_matches_sha256(self):
        raw = "999999"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert _hash_code(raw) == expected


# ── request_otp() ──────────────────────────────────────────────────────────────

class TestRequestOtp:

    @pytest.mark.asyncio
    async def test_creates_otp_record(self, db_session: AsyncSession):
        with patch(
            "app.services.auth.otp_service._send_email_otp",
            new_callable=AsyncMock,
        ):
            await request_otp(db_session, "new@example.com", purpose="verification")
            await db_session.flush()

        result = await db_session.execute(
            select(OtpCode).where(OtpCode.email == "new@example.com")
        )
        record = result.scalar_one_or_none()
        assert record is not None
        assert record.used is False
        assert record.attempt_count == 0
        # SQLite strips timezone info on storage — compare naive to naive.
        # PostgreSQL preserves tz info so this comparison would use _now() there.
        assert record.expires_at > _now_naive()

    @pytest.mark.asyncio
    async def test_burns_prior_unused_codes(self, db_session: AsyncSession):
        email = "burntest@example.com"

        # Insert two existing unused codes directly
        old1 = _make_otp_record(db_session, email, raw_code="111111")
        old2 = _make_otp_record(db_session, email, raw_code="222222")
        await db_session.flush()

        with patch(
            "app.services.auth.otp_service._send_email_otp",
            new_callable=AsyncMock,
        ):
            await request_otp(db_session, email, purpose="verification")
            await db_session.flush()

        # Refresh to get updated state
        await db_session.refresh(old1)
        await db_session.refresh(old2)

        assert old1.used is True, "First old code should be marked used"
        assert old2.used is True, "Second old code should be marked used"

        # New active code exists
        result = await db_session.execute(
            select(OtpCode).where(
                OtpCode.email == email,
                OtpCode.used == False,  # noqa: E712
            )
        )
        active = result.scalars().all()
        assert len(active) == 1, "Exactly one active code should remain"

    @pytest.mark.asyncio
    async def test_blocks_existing_email_on_verification(self, db_session: AsyncSession):
        """
        request_otp(purpose="verification") must reject emails that already
        have a registered User row — prevents re-registration attempts.
        """
        org, user = await make_org_with_owner(
            db_session,
            user_kwargs=dict(email="registered@example.com"),
        )

        with pytest.raises(OtpError) as exc_info:
            await request_otp(
                db_session, "registered@example.com", purpose="verification"
            )

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_2fa_email_skips_existing_user_guard(self, db_session: AsyncSession):
        """
        purpose="2fa_email" is for users who already exist — the guard
        must NOT fire even if the email is registered.
        """
        org, user = await make_org_with_owner(
            db_session,
            user_kwargs=dict(email="existing2fa@example.com"),
        )

        with patch(
            "app.services.auth.otp_service._send_email_otp",
            new_callable=AsyncMock,
        ) as mock_send:
            # Should not raise
            await request_otp(
                db_session, "existing2fa@example.com", purpose="2fa_email"
            )
            mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sms_raises_501_immediately(self, db_session: AsyncSession):
        with pytest.raises(OtpError) as exc_info:
            await request_otp(db_session, "sms@example.com", purpose="2fa_sms")

        assert exc_info.value.status_code == 501

        # No OtpCode row should have been created
        result = await db_session.execute(
            select(OtpCode).where(OtpCode.email == "sms@example.com")
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_email_delivery_failure_raises_503(self, db_session: AsyncSession):
        with patch(
            "app.services.auth.otp_service._send_email_otp",
            new_callable=AsyncMock,
            side_effect=OtpError(
                "Failed to send verification code. Please try again later.",
                503,
            ),
        ):
            with pytest.raises(OtpError) as exc_info:
                await request_otp(
                    db_session, "failmail@example.com", purpose="verification"
                )

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_email_is_normalized_before_storage(self, db_session: AsyncSession):
        """Email must be lowercased and stripped before the OtpCode is stored."""
        with patch(
            "app.services.auth.otp_service._send_email_otp",
            new_callable=AsyncMock,
        ):
            await request_otp(
                db_session, "  UPPER@Example.COM  ", purpose="verification"
            )
            await db_session.flush()

        result = await db_session.execute(
            select(OtpCode).where(OtpCode.email == "upper@example.com")
        )
        assert result.scalar_one_or_none() is not None


# ── verify_otp() ───────────────────────────────────────────────────────────────

class TestVerifyOtp:

    @pytest.mark.asyncio
    async def test_correct_code_returns_jwt(self, db_session: AsyncSession):
        email = "verify@example.com"
        raw_code = "456789"
        _make_otp_record(db_session, email, raw_code=raw_code)
        await db_session.flush()

        token = await verify_otp(db_session, email, raw_code)
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio
    async def test_jwt_carries_email_verification_type(self, db_session: AsyncSession):
        email = "jwttype@example.com"
        raw_code = "112233"
        _make_otp_record(db_session, email, raw_code=raw_code)
        await db_session.flush()

        token = await verify_otp(db_session, email, raw_code)
        payload = decode_token(token)

        assert payload.get("type") == TokenType.EMAIL_VERIFICATION
        assert payload.get("email") == email

    @pytest.mark.asyncio
    async def test_jwt_subject_is_email(self, db_session: AsyncSession):
        email = "jwtsub@example.com"
        raw_code = "998877"
        _make_otp_record(db_session, email, raw_code=raw_code)
        await db_session.flush()

        token = await verify_otp(db_session, email, raw_code)
        payload = decode_token(token)

        assert payload.get("sub") == email

    @pytest.mark.asyncio
    async def test_correct_code_marks_record_used(self, db_session: AsyncSession):
        email = "markused@example.com"
        raw_code = "555555"
        record = _make_otp_record(db_session, email, raw_code=raw_code)
        await db_session.flush()

        await verify_otp(db_session, email, raw_code)
        await db_session.refresh(record)

        assert record.used is True

    @pytest.mark.asyncio
    async def test_wrong_code_increments_attempt_count(self, db_session: AsyncSession):
        email = "attempts@example.com"
        record = _make_otp_record(db_session, email, raw_code="777777")
        await db_session.flush()

        with pytest.raises(OtpError):
            await verify_otp(db_session, email, "000000")

        await db_session.refresh(record)
        assert record.attempt_count == 1

    @pytest.mark.asyncio
    async def test_wrong_code_reports_remaining_attempts(self, db_session: AsyncSession):
        email = "remaining@example.com"
        _make_otp_record(db_session, email, raw_code="777777")
        await db_session.flush()

        with pytest.raises(OtpError) as exc_info:
            await verify_otp(db_session, email, "000000")

        remaining = OTP_MAX_ATTEMPTS - 1
        assert str(remaining) in exc_info.value.message

    @pytest.mark.asyncio
    async def test_max_attempts_locks_record(self, db_session: AsyncSession):
        """
        After OTP_MAX_ATTEMPTS wrong guesses the record must be locked
        (used=True) and a 429 must be raised.
        """
        email = "lockout@example.com"
        record = _make_otp_record(
            db_session,
            email,
            raw_code="888888",
            attempt_count=OTP_MAX_ATTEMPTS - 1,  # one guess away from lockout
        )
        await db_session.flush()

        with pytest.raises(OtpError) as exc_info:
            await verify_otp(db_session, email, "000000")  # wrong — triggers lockout

        assert exc_info.value.status_code == 429
        await db_session.refresh(record)
        assert record.used is True, "Record must be locked after max attempts"

    @pytest.mark.asyncio
    async def test_already_at_max_attempts_raises_429_without_incrementing(
        self, db_session: AsyncSession
    ):
        """
        If a record already has attempt_count == OTP_MAX_ATTEMPTS when
        verify_otp() is called, it must raise 429 immediately without
        incrementing further.
        """
        email = "alreadylocked@example.com"
        record = _make_otp_record(
            db_session,
            email,
            raw_code="888888",
            attempt_count=OTP_MAX_ATTEMPTS,
        )
        await db_session.flush()

        with pytest.raises(OtpError) as exc_info:
            await verify_otp(db_session, email, "000000")

        assert exc_info.value.status_code == 429
        await db_session.refresh(record)
        # attempt_count must not have gone above OTP_MAX_ATTEMPTS
        assert record.attempt_count == OTP_MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_expired_code_raises_error(self, db_session: AsyncSession):
        email = "expired@example.com"
        _make_otp_record(db_session, email, raw_code="321321", expired=True)
        await db_session.flush()

        with pytest.raises(OtpError) as exc_info:
            await verify_otp(db_session, email, "321321")

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_no_record_raises_error(self, db_session: AsyncSession):
        with pytest.raises(OtpError) as exc_info:
            await verify_otp(db_session, "ghost@example.com", "000000")

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_used_code_is_not_found(self, db_session: AsyncSession):
        """
        A code that was already verified (used=True) must be treated as
        non-existent — the query filters used=False.
        """
        email = "usedcode@example.com"
        _make_otp_record(db_session, email, raw_code="654654", used=True)
        await db_session.flush()

        with pytest.raises(OtpError) as exc_info:
            await verify_otp(db_session, email, "654654")

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_email_is_normalized_before_lookup(self, db_session: AsyncSession):
        """verify_otp must normalize the email before querying."""
        email = "normalize@example.com"
        raw_code = "101010"
        _make_otp_record(db_session, email, raw_code=raw_code)
        await db_session.flush()

        # Pass with uppercase and whitespace — should still find the record
        token = await verify_otp(db_session, "  NORMALIZE@Example.COM  ", raw_code)
        assert isinstance(token, str)

    @pytest.mark.asyncio
    async def test_most_recent_code_is_used_when_multiple_exist(
        self, db_session: AsyncSession
    ):
        """
        verify_otp orders by created_at desc and takes limit(1).
        The most recently created valid code must be the one checked.
        """
        email = "multicode@example.com"
        old_code = "111111"
        new_code = "999999"

        _make_otp_record(db_session, email, raw_code=old_code)
        await db_session.flush()
        _make_otp_record(db_session, email, raw_code=new_code)
        await db_session.flush()

        # The newer code should work
        token = await verify_otp(db_session, email, new_code)
        assert isinstance(token, str)