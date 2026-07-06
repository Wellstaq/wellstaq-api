"""
tests/services/auth/test_oauth_service.py

Unit tests for app/services/auth/oauth_service.py.

Strategy:
  oauth_login() takes pre-verified claims directly (no provider API calls
  inside the service), so tests just build claims dicts and assert on
  resulting User/UserAuthProvider state + AuthError branches.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import OAuthProvider, UserStatus
from app.models import User, UserActivityFeed, UserAuthProvider
from app.services.auth.oauth_service import oauth_login
from app.services.errors import AuthError

from tests.conftest import make_org_with_owner, make_user_with_role


# ── Local helpers ────────────────────────────────────────────────────────────

def _google_claims(email: str, sub: str = "google-sub-123", **overrides) -> dict:
    defaults = dict(
        email=email,
        email_verified=True,
        sub=sub,
        picture="https://example.com/avatar.png",
    )
    return {**defaults, **overrides}


def _apple_claims(email: str, sub: str = "apple-sub-123", **overrides) -> dict:
    defaults = dict(email=email, email_verified=True, sub=sub)
    return {**defaults, **overrides}


async def _make_active_user(db_session: AsyncSession, *, email: str, **overrides) -> tuple:
    org, owner = await make_org_with_owner(db_session)
    overrides.setdefault("status", UserStatus.ACTIVE)
    overrides.setdefault("branch_id", owner.branch_id)  # ← add this
    user = await make_user_with_role(
        db_session, organization_id=org.id, email=email, **overrides
    )
    db_session.add(user)
    await db_session.flush()
    return org, user

def _add_provider(db_session: AsyncSession, user: User, provider: str, provider_user_id: str):
    row = UserAuthProvider(
        user_id=user.id,
        organization_id=user.organization_id,
        provider=provider,
        provider_user_id=provider_user_id,
        access_token=None,
        refresh_token=None,
    )
    db_session.add(row)
    return row


def _call_kwargs(**overrides) -> dict:
    defaults = dict(
        device_name="TestDevice",
        device_fingerprint="test-fp",
        ip_address="127.0.0.1",
    )
    return {**defaults, **overrides}


# ── Email verification guard ─────────────────────────────────────────────────

class TestOauthLoginEmailVerifiedGuard:

    @pytest.mark.asyncio
    async def test_unverified_email_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="unverified@gmail.com")
        claims = _google_claims("unverified@gmail.com", email_verified=False)

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_email_verified_claim_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="noclaim@gmail.com")
        claims = {"email": "noclaim@gmail.com", "sub": "sub-1"}  # no email_verified key

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 400


# ── Missing claims guard ──────────────────────────────────────────────────────

class TestOauthLoginMissingClaims:

    @pytest.mark.asyncio
    async def test_missing_email_raises_400(self, db_session: AsyncSession):
        claims = _google_claims("placeholder@gmail.com")
        claims["email"] = ""

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_sub_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="nosub@gmail.com")
        claims = _google_claims("nosub@gmail.com", sub="")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 400


# ── No account found ───────────────────────────────────────────────────────────

class TestOauthLoginNoAccount:

    @pytest.mark.asyncio
    async def test_no_user_for_email_raises_404(self, db_session: AsyncSession):
        claims = _google_claims("nobody@gmail.com")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 404


# ── Status guards ──────────────────────────────────────────────────────────────

class TestOauthLoginStatusGuard:

    @pytest.mark.asyncio
    async def test_pending_user_raises_403(self, db_session: AsyncSession):
        org, user = await _make_active_user(
            db_session, email="pending@gmail.com", status=UserStatus.PENDING
        )
        claims = _google_claims("pending@gmail.com")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_suspended_user_raises_403(self, db_session: AsyncSession):
        org, user = await _make_active_user(
            db_session, email="suspended@gmail.com", status=UserStatus.SUSPENDED
        )
        claims = _google_claims("suspended@gmail.com")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_inactive_user_raises_403(self, db_session: AsyncSession):
        org, user = await _make_active_user(
            db_session, email="inactive@gmail.com", status=UserStatus.INACTIVE
        )
        claims = _google_claims("inactive@gmail.com")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 403


# ── Already-linked provider — login path ────────────────────────────────────

class TestOauthLoginAlreadyLinked:

    @pytest.mark.asyncio
    async def test_matching_sub_logs_in_successfully(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="linked@gmail.com")
        _add_provider(db_session, user, OAuthProvider.GOOGLE, "google-sub-abc")
        await db_session.flush()

        claims = _google_claims("linked@gmail.com", sub="google-sub-abc")
        result = await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())

        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_mismatched_sub_raises_401(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="hijack@gmail.com")
        _add_provider(db_session, user, OAuthProvider.GOOGLE, "real-sub")
        await db_session.flush()

        claims = _google_claims("hijack@gmail.com", sub="attacker-sub")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 401

    
# ── Auto-link — Google ────────────────────────────────────────────────────────

class TestOauthLoginGoogleAutoLink:

    @pytest.mark.asyncio
    async def test_gmail_address_auto_links_and_logs_in(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="newgoogle@gmail.com")
        claims = _google_claims("newgoogle@gmail.com", sub="fresh-sub")

        result = await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        await db_session.flush()

        assert "access_token" in result

        provider_result = await db_session.execute(
            select(UserAuthProvider).where(
                UserAuthProvider.user_id == user.id,
                UserAuthProvider.provider == OAuthProvider.GOOGLE,
            )
        )
        provider_row = provider_result.scalar_one()
        assert provider_row.provider_user_id == "fresh-sub"

    @pytest.mark.asyncio
    async def test_non_gmail_address_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="work@company.com")
        claims = _google_claims("work@company.com", sub="some-sub")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_auto_link_sets_avatar_from_picture_claim(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="avatar@gmail.com")
        claims = _google_claims(
            "avatar@gmail.com", sub="avatar-sub", picture="https://example.com/pic.jpg"
        )

        await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        await db_session.flush()
        await db_session.refresh(user)

        assert user.avatar_url == "https://example.com/pic.jpg"


# ── Apple — never auto-links ───────────────────────────────────────────────────

class TestOauthLoginApple:

    @pytest.mark.asyncio
    async def test_unlinked_apple_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="appleuser@example.com")
        claims = _apple_claims("appleuser@example.com")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, OAuthProvider.APPLE, claims, **_call_kwargs())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_linked_apple_logs_in_successfully(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="applelinked@example.com")
        _add_provider(db_session, user, OAuthProvider.APPLE, "apple-sub-xyz")
        await db_session.flush()

        claims = _apple_claims("applelinked@example.com", sub="apple-sub-xyz")
        result = await oauth_login(db_session, OAuthProvider.APPLE, claims, **_call_kwargs())

        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_apple_does_not_touch_avatar(self, db_session: AsyncSession):
        """avatar_url is only sourced from claims when provider == GOOGLE —
        Apple claims may carry a 'picture'-like field but it must be ignored."""
        org, user = await _make_active_user(db_session, email="applenoavatar@example.com")
        _add_provider(db_session, user, OAuthProvider.APPLE, "apple-sub-2")
        await db_session.flush()
        original_avatar = user.avatar_url

        claims = _apple_claims("applenoavatar@example.com", sub="apple-sub-2")
        claims["picture"] = "https://example.com/should-be-ignored.png"

        await oauth_login(db_session, OAuthProvider.APPLE, claims, **_call_kwargs())
        await db_session.flush()
        await db_session.refresh(user)

        assert user.avatar_url == original_avatar


# ── Unsupported provider ────────────────────────────────────────────────────────

class TestOauthLoginUnsupportedProvider:

    @pytest.mark.asyncio
    async def test_unknown_provider_raises_400(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="weird@example.com")
        claims = dict(email="weird@example.com", email_verified=True, sub="sub-weird")

        with pytest.raises(AuthError) as exc_info:
            await oauth_login(db_session, "facebook", claims, **_call_kwargs())
        assert exc_info.value.status_code == 400


# ── Audit logging ────────────────────────────────────────────────────────────────

class TestOauthLoginAudit:

    @pytest.mark.asyncio
    async def test_creates_login_audit_entry(self, db_session: AsyncSession):
        org, user = await _make_active_user(db_session, email="audit@gmail.com")
        _add_provider(db_session, user, OAuthProvider.GOOGLE, "audit-sub")
        await db_session.flush()

        claims = _google_claims("audit@gmail.com", sub="audit-sub")
        await oauth_login(db_session, OAuthProvider.GOOGLE, claims, **_call_kwargs())
        await db_session.flush()

        log_result = await db_session.execute(
            select(UserActivityFeed).where(UserActivityFeed.user_id == user.id)
        )
        assert log_result.scalar_one_or_none() is not None