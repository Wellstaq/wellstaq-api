"""Password-change and reset regressions using signed tokens and isolated Redis."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.auth.jwt import _create_token
from app.auth.security import hash_password, verify_password
from app.core.constants import TokenType
from app.services.user_setting import password_service
from app.services.errors import AuthError
from tests.conftest import make_org_with_owner


@pytest.fixture
def reset_email(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(password_service, "_send_reset_email", sender)
    return sender


class TestPasswordChange:
    async def test_correct_password_is_required(self, db_session):
        org, user = await make_org_with_owner(db_session, user_kwargs={"hashed_password": hash_password("Original123!")})
        before = user.hashed_password
        with pytest.raises(HTTPException) as exc:
            await password_service.change_password(session=db_session, user_id=user.id, current_password="Wrong123!", new_password="Replacement123!")
        assert exc.value.status_code == 400
        assert user.hashed_password == before

    async def test_success_stores_hash_not_plaintext(self, db_session):
        org, user = await make_org_with_owner(db_session, user_kwargs={"hashed_password": hash_password("Original123!")})
        await password_service.change_password(session=db_session, user_id=user.id, current_password="Original123!", new_password="Replacement123!")
        assert user.hashed_password != "Replacement123!"
        assert verify_password("Replacement123!", user.hashed_password)
        assert not verify_password("Original123!", user.hashed_password)


class TestPasswordReset:
    async def test_unknown_email_is_a_silent_noop(self, db_session, reset_email):
        assert await password_service.request_password_reset(session=db_session, email="unknown@example.com") is None
        reset_email.assert_not_awaited()

    async def test_reset_link_is_single_use_and_key_does_not_expose_token(self, db_session, memory_redis, reset_email):
        org, user = await make_org_with_owner(db_session)
        await password_service.request_password_reset(session=db_session, email=" OWNER@example.com ")
        reset_email.assert_awaited_once()
        token = reset_email.await_args.kwargs["reset_token"]
        assert 20 <= len(token) <= 24
        assert await memory_redis.get(password_service._reset_token_key(reset_token=token)) == user.email
        await password_service.reset_password(session=db_session, reset_token=token, new_password="Replacement123!")
        assert verify_password("Replacement123!", user.hashed_password)
        key = password_service._used_token_key(reset_token=token)
        assert token not in key
        assert 0 < await memory_redis.ttl(key) <= password_service.PASSWORD_RESET_TOKEN_TTL_MINUTES * 60
        with pytest.raises(HTTPException, match="already been used"):
            await password_service.reset_password(session=db_session, reset_token=token, new_password="Another123!")
        assert verify_password("Replacement123!", user.hashed_password)

    async def test_reset_email_includes_copyable_absolute_link(self, monkeypatch):
        sender = AsyncMock()
        monkeypatch.setattr("app.core.email.send_email", sender)

        await password_service._send_reset_email(
            email="user@example.com", reset_token="short-token"
        )

        html = sender.await_args.kwargs["html"]
        expected_url = "https://appwellstaq6.vercel.app/reset-password?token=short-token"
        assert "You can also copy this link manually to your browser:" in html
        assert expected_url in html

    @pytest.mark.parametrize("token_type,minutes", [(TokenType.ACCESS, 5), (TokenType.PASSWORD_RESET, -1)])
    async def test_wrong_type_and_expired_tokens_are_rejected(self, db_session, token_type, minutes):
        token = _create_token(subject="owner@example.com", expires_delta=timedelta(minutes=minutes), token_type="access", extra_claims={"type": token_type, "email": "owner@example.com"})
        with pytest.raises(HTTPException) as exc:
            await password_service.reset_password(session=db_session, reset_token=token, new_password="Replacement123!")
        assert exc.value.status_code == 400

    async def test_weak_password_does_not_consume_reset_link(self, db_session, memory_redis, reset_email):
        org, user = await make_org_with_owner(db_session)
        before = user.hashed_password
        await password_service.request_password_reset(session=db_session, email=user.email)
        token = reset_email.await_args.kwargs["reset_token"]
        with pytest.raises(AuthError):
            await password_service.reset_password(session=db_session, reset_token=token, new_password="weak")
        assert user.hashed_password == before
        assert await memory_redis.get(password_service._used_token_key(reset_token=token)) is None
        await password_service.reset_password(session=db_session, reset_token=token, new_password="Replacement123!")
        assert verify_password("Replacement123!", user.hashed_password)
