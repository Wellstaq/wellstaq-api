"""
tests/services/auth/test_signup_service.py

Unit tests for app/services/auth/signup_service.py.

Strategy:
  org_owner_signup() requires a valid email_verification_token. Rather than
  mocking jwt internals, we generate real tokens via the same helper the
  OTP flow uses (_make_email_verification_token), so tests exercise the
  real decode path end-to-end.

  org_owner_signup() also calls start_trial() internally, which looks up
  the single active SubscriptionPlan (SubscriptionPlan.is_active == True)
  to start the org's trial against. In production that row is seeded via
  a script that also registers the plan with the payment gateway — not
  something this test suite exercises — so an autouse fixture seeds a
  minimal active SubscriptionPlan directly before every test here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from app.models import RolePermission
from app.services.auth.token_service import load_user_permissions
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token
from app.core.constants import ActivityType, RoleName, UserStatus
from app.models import (
    Organization,
    Permission,
    Role,
    User,
    UserActivityFeed,
    UserAuthProvider,
    UserPermission,
    UserSettings,
)
from app.models.payment import SubscriptionPlan
from app.services.auth.otp_service import _make_email_verification_token
from app.services.auth.signup_service import org_owner_signup
from app.services.errors import AuthError


# ── Helpers ────────────────────────────────────────────────────────────────────

def _valid_token(email: str) -> str:
    """Generate a real email_verification_token the same way OTP does."""
    return _make_email_verification_token(email)


def _signup_kwargs(**overrides) -> dict:
    defaults = dict(
        first_name="Jane",
        last_name="Doe",
        password="StrongPass123!",
        business_name="Wellstaq Test Co",
        phone_number="+15550001111",
        employee_count=50,
        organization_type="startup",
        work_model="remote",
        device_name="TestDevice",
        device_fingerprint="test-fp",
        ip_address="127.0.0.1",
    )
    return {**defaults, **overrides}


async def _seed_super_admin_role(db_session: AsyncSession) -> Role:
    role = Role(
        name=RoleName.SUPER_ADMIN,
        organization_id=None,
        branch_id=None,
        is_system=True,
    )
    db_session.add(role)
    await db_session.flush()
    return role

@pytest.fixture(autouse=True)
async def _seed_active_plan(db_session: AsyncSession):
    """org_owner_signup() -> start_trial() requires a SubscriptionPlan named
    'Trial' with is_active == True. Production seeds this via a script that
    also talks to the payment gateway; here we just need the local row."""
    plan = SubscriptionPlan(
        name="Trial",         
        billing_interval="monthly",
        amount=0,
        currency="USD",
        features={},
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()
    return plan

# ── Core happy path ────────────────────────────────────────────────────────────

class TestOrgOwnerSignupHappyPath:

    @pytest.mark.asyncio
    async def test_creates_org_and_owner_correctly_linked(
        self, db_session: AsyncSession
    ):
        await _seed_super_admin_role(db_session)
        token = _valid_token("owner1@example.com")

        result = await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Linked Co")
        )
        await db_session.flush()

        org_result = await db_session.execute(
            select(Organization).where(Organization.slug == "linked-co")
        )
        org = org_result.scalar_one()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner1@example.com")
        )
        user = user_result.scalar_one()

        assert org.owner_id == user.id
        assert user.organization_id == org.id

    @pytest.mark.asyncio
    async def test_owner_status_is_active_immediately(
        self, db_session: AsyncSession
    ):
        await _seed_super_admin_role(db_session)
        token = _valid_token("owner2@example.com")

        await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Active Co")
        )
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner2@example.com")
        )
        user = user_result.scalar_one()
        assert user.status == UserStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_owner_role_is_super_admin(self, db_session: AsyncSession):
        role = await _seed_super_admin_role(db_session)
        token = _valid_token("owner3@example.com")

        await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Role Co")
        )
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner3@example.com")
        )
        user = user_result.scalar_one()
        assigned = await db_session.get(Role, user.role_id)
        assert assigned.id != role.id
        assert assigned.template_role_id == role.id
        assert assigned.organization_id == user.organization_id
        assert assigned.name == RoleName.SUPER_ADMIN

    @pytest.mark.asyncio
    async def test_owner_receives_all_permissions_org_wide(
        self, db_session: AsyncSession
    ):
        role = await _seed_super_admin_role(db_session)
        perm1 = Permission(name="department.create", category="department", scope="org")
        perm2 = Permission(name="post.delete", category="content", scope="both")
        db_session.add_all([perm1, perm2])
        await db_session.flush()
        db_session.add_all([RolePermission(role_id=role.id, permission_id=p.id, scope="org") for p in (perm1, perm2)])
        await db_session.flush()

        token = _valid_token("owner4@example.com")
        await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Perm Co")
        )
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner4@example.com")
        )
        user = user_result.scalar_one()

        grants_result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        grants = grants_result.scalars().all()

        assert grants == []  # no duplicated direct user grants
        effective = await load_user_permissions(db_session, user.id, user.organization_id)
        assert {(g.name, g.branch_id) for g in effective} == {("department.create", None), ("post.delete", None)}

    @pytest.mark.asyncio
    async def test_creates_password_auth_provider(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token = _valid_token("owner5@example.com")

        await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Auth Co")
        )
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner5@example.com")
        )
        user = user_result.scalar_one()

        provider_result = await db_session.execute(
            select(UserAuthProvider).where(UserAuthProvider.user_id == user.id)
        )
        provider = provider_result.scalar_one()
        assert provider.provider == "password"
        assert provider.access_token is None
        assert provider.refresh_token is None

    @pytest.mark.asyncio
    async def test_creates_user_settings(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token = _valid_token("owner6@example.com")

        await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Settings Co")
        )
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner6@example.com")
        )
        user = user_result.scalar_one()

        settings_result = await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )
        assert settings_result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_creates_audit_log_entry(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token = _valid_token("owner7@example.com")

        await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Audit Co")
        )
        await db_session.flush()

        user_result = await db_session.execute(
            select(User).where(User.email == "owner7@example.com")
        )
        user = user_result.scalar_one()

        log_result = await db_session.execute(
            select(UserActivityFeed).where(
                UserActivityFeed.user_id == user.id,
                UserActivityFeed.activity_type == ActivityType.ACCOUNT_CREATED,
            )
        )
        assert log_result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_returns_token_pair_and_slug(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token = _valid_token("owner8@example.com")

        result = await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Token Co")
        )

        assert "access_token" in result
        assert "refresh_token" in result
        assert result["token_type"] == "bearer"
        assert result["slug"] == "token-co"

    @pytest.mark.asyncio
    async def test_access_token_decodes_with_correct_claims(
        self, db_session: AsyncSession
    ):
        role = await _seed_super_admin_role(db_session)
        perm = Permission(name="org.deactivate", category="org", scope="org")
        db_session.add(perm)
        await db_session.flush()
        db_session.add(RolePermission(role_id=role.id, permission_id=perm.id, scope="org"))
        await db_session.flush()

        token = _valid_token("owner9@example.com")
        result = await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="Claims Co")
        )

        payload = decode_token(result["access_token"])
        assert payload["role"] == RoleName.SUPER_ADMIN
        assert {"name": "org.deactivate", "branch_id": None} in payload["permissions"]


# ── Validation / error paths ────────────────────────────────────────────────────

class TestOrgOwnerSignupValidation:

    @pytest.mark.asyncio
    async def test_invalid_token_raises_auth_error(self, db_session: AsyncSession):
        with pytest.raises(AuthError):
            await org_owner_signup(
                db_session, "not-a-real-token", **_signup_kwargs()
            )

    @pytest.mark.asyncio
    async def test_duplicate_email_raises_409(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token1 = _valid_token("dupe@example.com")
        await org_owner_signup(
            db_session, token1, **_signup_kwargs(business_name="First Co")
        )
        await db_session.flush()

        token2 = _valid_token("dupe@example.com")
        with pytest.raises(AuthError) as exc_info:
            await org_owner_signup(
                db_session, token2, **_signup_kwargs(business_name="Second Co")
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_duplicate_business_name_raises_409(
        self, db_session: AsyncSession
    ):
        await _seed_super_admin_role(db_session)
        token1 = _valid_token("biz1@example.com")
        await org_owner_signup(
            db_session, token1,
            **_signup_kwargs(business_name="Same Biz Name"),
        )
        await db_session.flush()

        token2 = _valid_token("biz2@example.com")
        with pytest.raises(AuthError) as exc_info:
            await org_owner_signup(
                db_session, token2,
                **_signup_kwargs(business_name="Same Biz Name"),
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_duplicate_phone_raises_409(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token1 = _valid_token("phone1@example.com")
        await org_owner_signup(
            db_session, token1,
            **_signup_kwargs(business_name="Phone Co A", phone_number="+15559990000"),
        )
        await db_session.flush()

        token2 = _valid_token("phone2@example.com")
        with pytest.raises(AuthError) as exc_info:
            await org_owner_signup(
                db_session, token2,
                **_signup_kwargs(
                    business_name="Phone Co B", phone_number="+15559990000"
                ),
            )
        assert exc_info.value.status_code == 409


# ── Normalization ──────────────────────────────────────────────────────────────

class TestOrgOwnerSignupNormalization:

    @pytest.mark.asyncio
    async def test_business_name_is_stripped(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token = _valid_token("strip1@example.com")

        await org_owner_signup(
            db_session, token,
            **_signup_kwargs(business_name="   Padded Name   "),
        )
        await db_session.flush()

        result = await db_session.execute(
            select(Organization).where(Organization.slug == "padded-name")
        )
        org = result.scalar_one()
        assert org.business_name == "Padded Name"

    @pytest.mark.asyncio
    async def test_phone_number_is_stripped(self, db_session: AsyncSession):
        await _seed_super_admin_role(db_session)
        token = _valid_token("strip2@example.com")

        await org_owner_signup(
            db_session, token,
            **_signup_kwargs(
                business_name="Strip Phone Co",
                phone_number="   +15551112222   ",
            ),
        )
        await db_session.flush()

        result = await db_session.execute(
            select(User).where(User.email == "strip2@example.com")
        )
        user = result.scalar_one()
        assert user.phone_number == "+15551112222"


# ── Missing system role edge case ──────────────────────────────────────────────

class TestOrgOwnerSignupMissingRole:

    @pytest.mark.asyncio
    async def test_signup_succeeds_by_self_healing_when_role_not_preseeded(
        self, db_session: AsyncSession
    ):
        """
        If the super_admin system role hasn't been seeded yet, signup
        should self-heal (re-run the idempotent role seed) rather than
        creating an owner with no role/permissions, or hard-failing on
        what's usually just a fresh-deploy ordering issue.
        """
        token = _valid_token("norole@example.com")

        result = await org_owner_signup(
            db_session, token, **_signup_kwargs(business_name="No Role Co")
        )

        user_result = await db_session.execute(
            select(User).where(User.email == "norole@example.com")
        )
        user = user_result.scalar_one()
        assert user.role_id is not None  # self-heal assigned the role, not left null
