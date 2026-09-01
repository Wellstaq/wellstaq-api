"""
tests/services/auth/test_invite_service.py

Unit tests for app/services/auth/invite_service.py.

Strategy:
  - request_invite_otp: monkeypatch app.core.email.send_email (no real
    delivery in tests) and assert a Redis OTP record is created.
  - verify_invite_otp: rather than mocking email to capture the code,
    insert a Redis OTP record using otp_service's own _hash_code
    helper — this exercises the real verify path without depending on
    delivery plumbing.
  - invite_register: drive it through a real invite_verification_token
    via _make_invite_verification_token, same pattern test_signup_service
    uses for email_verification_token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
import json

from app.core.constants import InviteStatus, UserStatus
from app.models import (
    OrganizationInvite,
    User, 
    UserActivityFeed,
    UserAuthProvider, 
    UserSettings,
    DepartmentMember,
    Department,
    Role, Permission, RolePermission, UserRoleAssignment,
  
)
from app.services.auth._helpers import _make_invite_verification_token
from app.services.auth.invite_service import (
    invite_register,
    request_invite_otp,
    verify_invite_otp,
)
from app.services.errors import AuthError, OtpError
from app.core.redis import get_redis
from app.core.constants import OTP_TTL_MINUTES
from app.services.auth.otp_service import _hash_code, _otp_key
from tests.conftest import make_org_with_owner, make_user_with_role, get_or_create_system_role


# ── Local factories — specific to invite tests, not reused elsewhere ────────
def make_department(organization_id: str, branch_id: str, **overrides) -> Department:
    """Build a Department instance (not yet persisted)."""
    defaults = dict(name="Engineering")
    return Department(
        organization_id=organization_id,
        branch_id=branch_id,  
        **{**defaults, **overrides}
    )

def make_invite(
    *,
    organization_id: str,
    invited_department_id: str,
    invited_role_id: str,
    invited_by: str,
    branch_id: str,  # Make this required (no default)
    invited_email: str = "invitee@example.com",
    status: str = InviteStatus.PENDING,
    expires_in: timedelta = timedelta(days=7),
    **overrides,
) -> OrganizationInvite:
    """
    Build an OrganizationInvite (not yet persisted). invite_code is a
    real six-digit value (matches what send_invite() generates) so
    tests exercise the real lookup-by-code path rather than a fake string.
    """
    now = datetime.now(timezone.utc)
    defaults = dict(
        invite_code=f"{secrets.randbelow(1_000_000):06d}",
        invited_email=invited_email.lower().strip(),
        branch_id=branch_id,  # Now guaranteed to be provided
        status=status,
        expires_at=now + expires_in,
    )
    return OrganizationInvite(
        organization_id=organization_id,
        invited_department_id=invited_department_id,
        invited_role_id=invited_role_id,
        invited_by=invited_by,
        **{**defaults, **overrides}
    )

# ── Shared setup helper ─────────────────────────────────────────────────────
async def _setup_org_dept_invite(db_session: AsyncSession, **invite_overrides):
    """Org + owner + department + a pending invite, all persisted."""
    org, owner = await make_org_with_owner(db_session)
    await get_or_create_system_role(db_session, "member")
    from app.services.organization_roles import ensure_organization_roles
    await ensure_organization_roles(db_session, org.id)
    await db_session.flush()
    invited_role = Role(name="team_leader", organization_id=org.id, branch_id=owner.branch_id, is_system=False)
    permission = Permission(name="overview", category="dashboard", scope="both")
    db_session.add_all([invited_role, permission])
    await db_session.flush()
    db_session.add(RolePermission(role_id=invited_role.id, permission_id=permission.id, scope="branch"))
    await db_session.flush()
    
    # Create department with the owner's branch
    dept = make_department(organization_id=org.id, branch_id=owner.branch_id)
    db_session.add(dept)
    await db_session.flush()

    invite = make_invite(
        organization_id=org.id,
        invited_department_id=dept.id,
        invited_role_id=invited_role.id,
        invited_by=owner.id,
        branch_id=owner.branch_id,  # Add this required field
        **invite_overrides,
    )
    db_session.add(invite)
    await db_session.flush()
    return org, owner, dept, invite

@pytest.fixture(autouse=True)
def mock_send_email(monkeypatch):
    """No real email delivery in tests."""
    async def _noop(*args, **kwargs):
        return None
    monkeypatch.setattr("app.core.email.send_email", _noop)


async def _seed_otp(email: str, raw_code: str, purpose: str = "verification") -> None:
    """Insert a verifiable OTP directly into Redis, bypassing email delivery."""
    redis = await get_redis()
    payload = json.dumps({"code_hash": _hash_code(raw_code), "attempt_count": 0})
    await redis.set(_otp_key(email=email.lower().strip(), purpose=purpose), payload, ex=OTP_TTL_MINUTES * 60)

# ── request_invite_otp ───────────────────────────────────────────────────────

class TestRequestInviteOtp:

    @pytest.mark.asyncio
    async def test_sends_otp_for_valid_pending_invite(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="newhire@example.com"
        )

        masked = await request_invite_otp(db_session, invite.invite_code)
        await db_session.flush()

        assert masked == "n***@example.com"

        redis = await get_redis()
        raw = await redis.get(_otp_key(email="newhire@example.com", purpose="verification"))
        assert raw is not None

    
    @pytest.mark.asyncio
    async def test_unknown_invite_code_raises_404(self, db_session: AsyncSession):
        with pytest.raises(AuthError) as exc_info:
            await request_invite_otp(db_session, "not-a-real-code")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_expired_invite_raises_410(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, expires_in=timedelta(days=-1)
        )
        with pytest.raises(AuthError) as exc_info:
            await request_invite_otp(db_session, invite.invite_code)
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_already_accepted_invite_raises_404(self, db_session: AsyncSession):
        # _load_pending_invite filters status==PENDING in the WHERE clause,
        # so a non-pending invite_code simply doesn't match -> 404, not 409.
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, status=InviteStatus.ACCEPTED
        )
        with pytest.raises(AuthError) as exc_info:
            await request_invite_otp(db_session, invite.invite_code)
        assert exc_info.value.status_code == 404

   
    @pytest.mark.asyncio
    async def test_existing_account_for_invited_email_raises_409(
        self, db_session: AsyncSession
        ):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="taken@example.com"
        )
        existing = await make_user_with_role(
            db_session, 
            organization_id=org.id, 
            email="taken@example.com",
            branch_id=owner.branch_id,  # Add this
        )
        db_session.add(existing)
        await db_session.flush()
    
        with pytest.raises(AuthError) as exc_info:
            await request_invite_otp(db_session, invite.invite_code)
        assert exc_info.value.status_code == 409


# ── verify_invite_otp ─────────────────────────────────────────────────────────

class TestVerifyInviteOtp:

    @pytest.mark.asyncio
    async def test_correct_code_returns_invite_verification_token(
        self, db_session: AsyncSession
    ):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="verify1@example.com"
        )
        await _seed_otp("verify1@example.com", "111222")

        token = await verify_invite_otp(db_session, invite.invite_code, "111222")
        assert isinstance(token, str) and token

    @pytest.mark.asyncio
    async def test_wrong_code_raises_otp_error(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="verify2@example.com"
        )
        await _seed_otp("verify1@example.com", "111222")   # ← wrong email, should be verify2
        with pytest.raises(OtpError):
            await verify_invite_otp(db_session, invite.invite_code, "999999")

    @pytest.mark.asyncio
    async def test_invalid_invite_code_raises_auth_error(self, db_session: AsyncSession):
        with pytest.raises(AuthError):
            await verify_invite_otp(db_session, "bogus-code", "111222")


# ── invite_register ───────────────────────────────────────────────────────────

def _register_kwargs(**overrides) -> dict:
    defaults = dict(
        first_name="New",
        last_name="Hire",
        password="StrongPass123!",
        country="NG",
        state="FCT",
        baseline={"entries": [
            {"dimension": "sleep", "level": 3, "reason": "ok"},
        ]},
        # "focus" is not a member of PRIORITY_VOCABULARY
        # (reduce_stress, build_energy, improve_balance, save_better,
        # stay_active, feel_connected) — the DB CHECK constraint
        # ck_user_priorities_priority_vocab rejects anything outside
        # that set, so this must be one of those real values.
        priorities=[{"priority": "reduce_stress", "rank": 1}],
        device_name="TestDevice",
        device_fingerprint="test-fp",
        ip_address="127.0.0.1",
    )
    return {**defaults, **overrides}


class TestInviteRegisterHappyPath:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("role_name,overview", [("member", False), ("custom_viewer", False), ("custom_viewer", True)])
    async def test_dashboard_session_requires_overview_permission(self, db_session, role_name, overview):
        org, owner, dept, invite = await _setup_org_dept_invite(db_session, invited_email="access-check@example.com")
        role = await db_session.get(Role, invite.invited_role_id)
        role.name = role_name
        if not overview:
            await db_session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        await db_session.flush()
        token = _make_invite_verification_token(invite.invited_email, invite.invite_code)
        result = await invite_register(db_session, token, **_register_kwargs())
        assert result["requires_app"] is (not overview)
        assert bool(result.get("access_token")) is overview
        assert bool(result.get("refresh_token")) is overview


    @pytest.mark.asyncio
    async def test_creates_active_user_linked_to_org_branch_role(
        self, db_session: AsyncSession
    ):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg1@example.com"
        )
        token = _make_invite_verification_token("reg1@example.com", invite.invite_code)

        result = await invite_register(db_session, token, **_register_kwargs())
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "reg1@example.com")
        )
        user = user_result.scalar_one()

        assert user.status == UserStatus.ACTIVE
        assert user.organization_id == org.id
        assignment = (await db_session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))).scalar_one()
        assert assignment.role_id == invite.invited_role_id
        assert assignment.branch_id == invite.branch_id
        org_role = await db_session.get(Role, user.role_id)
        assert org_role.name == "member"
        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_creates_department_membership(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg2@example.com"
        )
        token = _make_invite_verification_token("reg2@example.com", invite.invite_code)

        await invite_register(db_session, token, **_register_kwargs())
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "reg2@example.com")
        )
        user = user_result.scalar_one()

        member_result = await db_session.execute(
            select(DepartmentMember).where(DepartmentMember.user_id == user.id)
        )
        membership = member_result.scalar_one()
        assert membership.department_id == dept.id

    @pytest.mark.asyncio
    async def test_marks_invite_accepted_with_accepted_by(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg3@example.com"
        )
        token = _make_invite_verification_token("reg3@example.com", invite.invite_code)

        await invite_register(db_session, token, **_register_kwargs())
        await db_session.flush()
        await db_session.refresh(invite)

        user_result = await db_session.execute(
            select(User).where(User.email == "reg3@example.com")
        )
        user = user_result.scalar_one()

        assert invite.status == InviteStatus.ACCEPTED
        assert invite.accepted_by_id == user.id
        assert invite.accepted_at is not None

    @pytest.mark.asyncio
    async def test_creates_password_auth_provider_and_settings(
        self, db_session: AsyncSession
    ):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg4@example.com"
        )
        token = _make_invite_verification_token("reg4@example.com", invite.invite_code)

        await invite_register(db_session, token, **_register_kwargs())
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "reg4@example.com")
        )
        user = user_result.scalar_one()

        provider_result = await db_session.execute(
            select(UserAuthProvider).where(UserAuthProvider.user_id == user.id)
        )
        assert provider_result.scalar_one_or_none() is not None

        settings_result = await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )
        assert settings_result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_creates_audit_log_entry(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg5@example.com"
        )
        token = _make_invite_verification_token("reg5@example.com", invite.invite_code)

        await invite_register(db_session, token, **_register_kwargs())
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "reg5@example.com")
        )
        user = user_result.scalar_one()

        log_result = await db_session.execute(
            select(UserActivityFeed).where(UserActivityFeed.user_id == user.id)
        )
        assert log_result.scalar_one_or_none() is not None


class TestInviteRegisterValidation:

    @pytest.mark.asyncio
    async def test_invalid_token_raises_auth_error(self, db_session: AsyncSession):
        with pytest.raises(AuthError):
            await invite_register(db_session, "not-a-real-token", **_register_kwargs())

    @pytest.mark.asyncio
    async def test_expired_invite_raises_410(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session,
            invited_email="reg6@example.com",
            expires_in=timedelta(days=-1),
        )
        token = _make_invite_verification_token("reg6@example.com", invite.invite_code)

        with pytest.raises(AuthError) as exc_info:
            await invite_register(db_session, token, **_register_kwargs())
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_existing_account_for_invited_email_raises_409(
        self, db_session: AsyncSession
        ):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="taken@example.com"
        )
        existing = await make_user_with_role(
            db_session,
            organization_id=org.id,
            email="taken@example.com",
            branch_id=owner.branch_id,
        )
        db_session.add(existing)
        await db_session.flush()

        token = _make_invite_verification_token("taken@example.com", invite.invite_code)
        with pytest.raises(AuthError) as exc_info:
            await invite_register(db_session, token, **_register_kwargs())
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_invalid_baseline_format_raises_400(self, db_session: AsyncSession):
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg8@example.com"
        )
        token = _make_invite_verification_token("reg8@example.com", invite.invite_code)

        with pytest.raises(AuthError) as exc_info:
            await invite_register(
                db_session, token,
                **_register_kwargs(baseline={"wrong_key": []}),
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_already_accepted_invite_cannot_be_registered_twice(
        self, db_session: AsyncSession
    ):
        """The core nonsense-state bug this refactor was about: an
        already-accepted invite must reject a second registration
        attempt, even with a freshly-minted token for the same email.

        """
        org, owner, dept, invite = await _setup_org_dept_invite(
            db_session, invited_email="reg9@example.com"
        )
        token1 = _make_invite_verification_token("reg9@example.com", invite.invite_code)
        await invite_register(db_session, token1, **_register_kwargs())
        await db_session.flush()

        token2 = _make_invite_verification_token("reg9@example.com", invite.invite_code)
        with pytest.raises(AuthError) as exc_info:
            await invite_register(db_session, token2, **_register_kwargs())
        assert exc_info.value.status_code == 404
