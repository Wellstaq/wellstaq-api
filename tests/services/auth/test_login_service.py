"""
tests/services/auth/test_login_service.py

Unit tests for app/services/auth/login_service.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token
from app.auth.security import hash_password
from app.core.constants import ActivityType, OAuthProvider, UserStatus
from app.models import User, UserActivityFeed, UserAuthProvider, UserSettings
from app.services.auth.login_service import password_login
from app.services.errors import AuthError
from tests.conftest import make_org_with_owner

RAW_PASSWORD = "correct-horse-battery-staple"


async def _add_password_provider(db_session: AsyncSession, user: User) -> None:
    db_session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=user.organization_id,
        provider=OAuthProvider.PASSWORD,
        provider_user_id=None,
        access_token=None,
        refresh_token=None,
    ))
    await db_session.flush()


async def _make_login_ready_user(
    db_session: AsyncSession,
    *,
    status: UserStatus = UserStatus.ACTIVE,
    two_fa_enabled: bool = False,
    with_password_provider: bool = True,
) -> tuple[User, str]:
    """
    Build an org + owner user with a real (hashed) password and a
    PASSWORD auth provider, ready for password_login(). Returns
    (user, email) for convenience.

    When two_fa_enabled=True, two_fa_method="totp" requires a non-null
    two_fa_secret per UserSettings' CHECK constraint — a dummy value is
    fine here since login_service only reads .two_fa_enabled/.method.
    """
    org, user = await make_org_with_owner(
        db_session,
        user_kwargs=dict(
            email="logintest@example.com",
            hashed_password=hash_password(RAW_PASSWORD),
            status=status,
        ),
    )

    if with_password_provider:
        await _add_password_provider(db_session, user)

    db_session.add(UserSettings(
        user_id=user.id,
        organization_id=user.organization_id,
        two_fa_enabled=two_fa_enabled,
        two_fa_method="totp" if two_fa_enabled else None,
        two_fa_secret="dummy-encrypted-secret" if two_fa_enabled else None,
    ))
    await db_session.flush()

    return user, user.email


# ── Happy path ──────────────────────────────────────────────────────────────────

class TestPasswordLoginSuccess:

    @pytest.mark.asyncio
    async def test_returns_token_pair_when_2fa_off(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)

        result = await password_login(db_session, email, RAW_PASSWORD)

        assert set(result.keys()) == {"access_token", "refresh_token", "token_type"}
        assert result["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_access_token_decodes_to_correct_user(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)

        result = await password_login(db_session, email, RAW_PASSWORD)
        payload = decode_token(result["access_token"])

        assert payload["sub"] == user.id
        assert payload["org_id"] == user.organization_id

    @pytest.mark.asyncio
    async def test_email_is_case_insensitive(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)

        result = await password_login(db_session, email.upper(), RAW_PASSWORD)
        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_writes_login_audit_log(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)
        await password_login(db_session, email, RAW_PASSWORD)

        result = await db_session.execute(
            select(UserActivityFeed).where(
                UserActivityFeed.user_id == user.id,
                UserActivityFeed.activity_type == ActivityType.LOGIN,
            )
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.activity_metadata.get("provider") == OAuthProvider.PASSWORD


# ── 2FA branch ────────────────────────────────────────────────────────────────────

class TestPasswordLoginTwoFactor:

    @pytest.mark.asyncio
    async def test_returns_challenge_not_tokens_when_2fa_on(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=True)

        result = await password_login(db_session, email, RAW_PASSWORD)

        assert result.get("requires_2fa") is True
        assert "access_token" not in result
        assert "refresh_token" not in result

    @pytest.mark.asyncio
    async def test_challenge_method_matches_settings(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=True)

        result = await password_login(db_session, email, RAW_PASSWORD)
        assert result["two_fa_method"] == "totp"

    @pytest.mark.asyncio
    async def test_2fa_login_does_not_write_login_audit_log(self, db_session: AsyncSession):
        """The LOGIN audit entry is only written on the non-2FA path —
        a 2FA challenge isn't a completed login yet."""
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=True)
        await password_login(db_session, email, RAW_PASSWORD)

        result = await db_session.execute(
            select(UserActivityFeed).where(
                UserActivityFeed.user_id == user.id,
                UserActivityFeed.activity_type == ActivityType.LOGIN,
            )
        )
        assert result.scalar_one_or_none() is None


# ── Failure: bad credentials ────────────────────────────────────────────────────────

class TestPasswordLoginBadCredentials:

    @pytest.mark.asyncio
    async def test_wrong_password_raises_401(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)

        with pytest.raises(AuthError) as exc_info:
            await password_login(db_session, email, "totally-wrong-password")

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_email_raises_401_with_same_message(
        self, db_session: AsyncSession
    ):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)

        with pytest.raises(AuthError) as wrong_pw:
            await password_login(db_session, email, "totally-wrong-password")

        with pytest.raises(AuthError) as no_user:
            await password_login(db_session, "ghost@example.com", "whatever")

        # Same status + message for both, so a bad actor can't distinguish
        # "wrong password" from "email not registered" (enumeration defense).
        assert wrong_pw.value.status_code == no_user.value.status_code == 401
        assert str(wrong_pw.value) == str(no_user.value)

    @pytest.mark.asyncio
    async def test_wrong_password_writes_failed_login_audit(
        self, db_session: AsyncSession
    ):
        user, email = await _make_login_ready_user(db_session, two_fa_enabled=False)

        with pytest.raises(AuthError):
            await password_login(db_session, email, "totally-wrong-password")

        result = await db_session.execute(
            select(UserActivityFeed).where(
                UserActivityFeed.user_id == user.id,
                UserActivityFeed.activity_type == ActivityType.FAILED_LOGIN,
            )
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.activity_metadata.get("reason") == "wrong_password"

    @pytest.mark.asyncio
    async def test_nonexistent_email_writes_no_audit_log(self, db_session: AsyncSession):
        """No user row to attach a log to — and logging would itself leak
        whether the email exists, so no audit row should be written at all."""
        with pytest.raises(AuthError):
            await password_login(db_session, "ghost@example.com", "whatever")

        result = await db_session.execute(
            select(UserActivityFeed).where(
                UserActivityFeed.activity_type == ActivityType.FAILED_LOGIN
            )
        )
        assert result.scalar_one_or_none() is None


# ── Failure: account status ───────────────────────────────────────────────────────────

class TestPasswordLoginAccountStatus:

    @pytest.mark.asyncio
    async def test_suspended_user_raises_403(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(
            db_session, status=UserStatus.SUSPENDED, two_fa_enabled=False
        )

        with pytest.raises(AuthError) as exc_info:
            await password_login(db_session, email, RAW_PASSWORD)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_inactive_user_raises_403(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(
            db_session, status=UserStatus.INACTIVE, two_fa_enabled=False
        )

        with pytest.raises(AuthError) as exc_info:
            await password_login(db_session, email, RAW_PASSWORD)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_pending_user_raises_403(self, db_session: AsyncSession):
        user, email = await _make_login_ready_user(
            db_session, status=UserStatus.PENDING, two_fa_enabled=False
        )

        with pytest.raises(AuthError) as exc_info:
            await password_login(db_session, email, RAW_PASSWORD)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_suspended_organization_blocks_active_user_login(self, db_session: AsyncSession):
        """A platform-admin org suspension (see app/services/platform_admin/
        organization_service.py) signs out every member immediately —
        even an otherwise-active user can't log in while it's suspended."""
        user, email = await _make_login_ready_user(db_session, status=UserStatus.ACTIVE, two_fa_enabled=False)
        from app.models import Organization
        org = (await db_session.execute(select(Organization).where(Organization.id == user.organization_id))).scalar_one()
        org.is_active = False
        await db_session.flush()

        with pytest.raises(AuthError) as exc_info:
            await password_login(db_session, email, RAW_PASSWORD)

        assert exc_info.value.status_code == 403


# ── Failure: OAuth-only account ───────────────────────────────────────────────────────

class TestPasswordLoginOAuthOnlyAccount:

    @pytest.mark.asyncio
    async def test_account_without_password_provider_raises_400(
        self, db_session: AsyncSession
    ):
        user, email = await _make_login_ready_user(
            db_session, two_fa_enabled=False, with_password_provider=False
        )

        with pytest.raises(AuthError) as exc_info:
            await password_login(db_session, email, RAW_PASSWORD)

        assert exc_info.value.status_code == 400