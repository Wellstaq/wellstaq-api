"""Redis-backed OTP tests: expiry, purpose isolation, delivery and one-time use.

Uses the shared in-memory DB, deterministic Redis and mocked email delivery.
"""
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from app.auth.jwt import decode_token
from app.core.constants import OTP_LENGTH, OTP_MAX_ATTEMPTS, OTP_TTL_MINUTES, TokenType
from app.services.auth import otp_service
from app.services.errors import OtpError
from tests.conftest import make_org_with_owner


@pytest.fixture
def delivery(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(otp_service, "_send_email_otp", sender)
    monkeypatch.setattr(otp_service, "_make_code", lambda: "123456")
    return sender


class TestCodeHelpers:
    def test_code_has_fixed_length_and_only_digits(self):
        for _ in range(50):
            code = otp_service._make_code()
            assert len(code) == OTP_LENGTH and code.isdigit()

    def test_hash_is_sha256(self):
        assert otp_service._hash_code("123456") == hashlib.sha256(b"123456").hexdigest()
        assert otp_service._hash_code("123456") != otp_service._hash_code("654321")


class TestRequestOtp:
    async def test_normalizes_email_and_stores_hash_with_expiry(self, db_session, memory_redis, delivery):
        await otp_service.request_otp(db_session, " NEW@example.com ")
        key = "otp:verification:new@example.com"
        assert json.loads(await memory_redis.get(key)) == {"code_hash": otp_service._hash_code("123456"), "attempt_count": 0}
        assert await memory_redis.ttl(key) == OTP_TTL_MINUTES * 60
        delivery.assert_awaited_once_with("new@example.com", "123456", "verification")

    async def test_resend_invalidates_previous_code(self, db_session, delivery, monkeypatch):
        await otp_service.request_otp(db_session, "new@example.com")
        monkeypatch.setattr(otp_service, "_make_code", lambda: "654321")
        await otp_service.request_otp(db_session, "new@example.com")
        with pytest.raises(OtpError, match="Incorrect"):
            await otp_service.verify_otp(db_session, "new@example.com", "123456")
        assert await otp_service.verify_otp(db_session, "new@example.com", "654321")

    async def test_existing_email_rejected_without_delivery(self, db_session, delivery):
        _, owner = await make_org_with_owner(db_session)
        with pytest.raises(OtpError) as exc:
            await otp_service.request_otp(db_session, owner.email)
        assert exc.value.status_code == 409
        delivery.assert_not_awaited()

    async def test_existing_user_can_request_two_factor(self, db_session, delivery):
        _, owner = await make_org_with_owner(db_session)
        await otp_service.request_otp(db_session, owner.email, "2fa_email")
        delivery.assert_awaited_once_with(owner.email, "123456", "2fa_email")

    async def test_sms_is_explicitly_unsupported(self, db_session, delivery):
        with pytest.raises(OtpError) as exc:
            await otp_service.request_otp(db_session, "new@example.com", "2fa_sms")
        assert exc.value.status_code == 501
        delivery.assert_not_awaited()


class TestVerifyOtp:
    async def test_success_returns_claims_and_consumes_code(self, db_session, memory_redis, delivery):
        await otp_service.request_otp(db_session, "new@example.com")
        token = await otp_service.verify_otp(db_session, " NEW@example.com ", " 123456 ")
        claims = decode_token(token)
        assert claims["email"] == "new@example.com"
        assert claims["type"] == TokenType.EMAIL_VERIFICATION
        assert await memory_redis.get("otp:verification:new@example.com") is None
        with pytest.raises(OtpError):
            await otp_service.verify_otp(db_session, "new@example.com", "123456")

    async def test_expired_code_rejected(self, db_session, memory_redis, delivery):
        await otp_service.request_otp(db_session, "new@example.com")
        memory_redis.advance(OTP_TTL_MINUTES * 60)
        with pytest.raises(OtpError, match="expired"):
            await otp_service.verify_otp(db_session, "new@example.com", "123456")

    async def test_unrequested_code_rejected(self, db_session):
        with pytest.raises(OtpError):
            await otp_service.verify_otp(db_session, "new@example.com", "123456")

    async def test_wrong_code_preserves_expiry(self, db_session, memory_redis, delivery):
        await otp_service.request_otp(db_session, "new@example.com")
        memory_redis.advance(30)
        with pytest.raises(OtpError, match="Incorrect"):
            await otp_service.verify_otp(db_session, "new@example.com", "000000")
        key = "otp:verification:new@example.com"
        assert await memory_redis.ttl(key) == OTP_TTL_MINUTES * 60 - 30
        assert json.loads(await memory_redis.get(key))["attempt_count"] == 1

    async def test_attempt_limit_locks_and_deletes_code(self, db_session, memory_redis, delivery):
        await otp_service.request_otp(db_session, "new@example.com")
        for attempt in range(OTP_MAX_ATTEMPTS):
            with pytest.raises(OtpError) as exc:
                await otp_service.verify_otp(db_session, "new@example.com", "000000")
            assert exc.value.status_code == (429 if attempt == OTP_MAX_ATTEMPTS - 1 else 400)
        assert await memory_redis.get("otp:verification:new@example.com") is None
        with pytest.raises(OtpError):
            await otp_service.verify_otp(db_session, "new@example.com", "123456")

    async def test_purposes_do_not_share_codes(self, db_session, memory_redis, delivery):
        await otp_service.request_otp(db_session, "new@example.com", "2fa_email")
        with pytest.raises(OtpError):
            await otp_service.verify_otp(db_session, "new@example.com", "123456", "verification")
        assert await memory_redis.get("otp:2fa_email:new@example.com") is not None
        assert await otp_service.verify_otp(db_session, "new@example.com", "123456", "2fa_email")


class TestEmailDelivery:
    async def test_provider_error_becomes_service_unavailable(self, monkeypatch):
        sender = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        monkeypatch.setattr("app.core.email.send_email", sender)
        with pytest.raises(OtpError) as exc:
            await otp_service._send_email_otp("new@example.com", "123456", "verification")
        assert exc.value.status_code == 503
