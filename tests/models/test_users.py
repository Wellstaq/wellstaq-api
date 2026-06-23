"""
User, UserSettings, UserAuthProvider, UserDevice tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed here — these are model/ORM-level tests.

Factory note:
make_user() and make_org_with_owner() live in conftest.py — shared
across every test file that needs a User/Organization. Only
UserSettings/UserDevice/UserAuthProvider factories, which are specific
to this file, stay local.
"""

import pytest
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserSettings, UserAuthProvider, UserDevice
from tests.conftest import make_user, make_org_with_owner


# ── Local factories ───────────────────────────────────────────────────────────

def make_settings(user_id: str, organization_id: str, **overrides) -> UserSettings:
    return UserSettings(user_id=user_id, organization_id=organization_id, **overrides)


def make_auth_provider(
    user_id: str, organization_id: str, provider: str = "password", **overrides
) -> UserAuthProvider:
    return UserAuthProvider(
        user_id=user_id, organization_id=organization_id, provider=provider, **overrides
    )


def make_device(user_id: str, organization_id: str, **overrides) -> UserDevice:
    defaults = dict(device_name="Chrome on macOS", ip_address="127.0.0.1")
    return UserDevice(
        user_id=user_id, organization_id=organization_id, **{**defaults, **overrides}
    )


# ════════════════════════════════════════════════
# User model
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_user_persists(db_session: AsyncSession):
    org, user = await make_org_with_owner(db_session)

    result = await db_session.execute(select(User).where(User.id == user.id))
    fetched = result.scalar_one()
    assert fetched.email == "owner@example.com"
    assert fetched.first_name == "Ada"
    assert fetched.organization_id == org.id


@pytest.mark.asyncio
async def test_user_id_is_valid_uuid(db_session: AsyncSession):
    """ID must be a valid parseable UUID — not just the right string shape."""
    _, user = await make_org_with_owner(db_session)
    assert str(UUID(user.id)) == user.id


@pytest.mark.asyncio
async def test_user_timestamps_auto_set(db_session: AsyncSession):
    _, user = await make_org_with_owner(db_session)
    assert user.created_at is not None
    assert user.updated_at is not None


@pytest.mark.asyncio
async def test_user_default_status_is_pending(db_session: AsyncSession):
    _, user = await make_org_with_owner(db_session)
    assert user.status == "pending"


@pytest.mark.asyncio
async def test_user_organization_id_required(db_session: AsyncSession):
    """NOT NULL — no user can exist without a resolved organization_id."""
    user = User(
        first_name="No",
        last_name="Org",
        email="noorg@example.com",
        role_name="employee",
        hashed_password="$2b$12$fakehash",
        organization_id=None,
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_organization_fk_must_exist(db_session: AsyncSession):
    """organization_id must reference a real organization."""
    user = User(
        first_name="Ghost",
        last_name="User",
        email="ghost@example.com",
        role_name="employee",
        hashed_password="$2b$12$fakehash",
        organization_id="00000000-0000-0000-0000-000000000000",
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_duplicate_email_raises(db_session: AsyncSession):
    org, _ = await make_org_with_owner(
        db_session, user_kwargs=dict(email="first@example.com")
    )
    db_session.add(make_user(organization_id=org.id, email="dup@example.com"))
    await db_session.flush()

    db_session.add(
        make_user(organization_id=org.id, email="dup@example.com", first_name="Other")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_optional_fields_nullable(db_session: AsyncSession):
    org, _ = await make_org_with_owner(
        db_session, user_kwargs=dict(email="owner-min@example.com")
    )
    user = make_user(organization_id=org.id, email="min@example.com")
    db_session.add(user)
    await db_session.flush()

    assert user.phone_number is None
    assert user.avatar_url is None
    assert user.country is None
    assert user.state is None
    assert user.branch_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role", ["super_admin", "branch_manager", "team_lead", "employee", "custom_role"]
)
async def test_user_accepts_any_role_name(db_session: AsyncSession, role: str):
    """role_name is a free string — no enum constraint."""
    _, user = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=f"org-{role}"),
        user_kwargs=dict(email=f"{role}@example.com", role_name=role),
    )
    assert user.role_name == role


# ── status constraint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "active", "inactive", "suspended"])
async def test_user_valid_statuses_accepted(db_session: AsyncSession, status: str):
    """All four known statuses must be accepted."""
    _, user = await make_org_with_owner(
        db_session,
        org_kwargs=dict(slug=f"org-{status}"),
        user_kwargs=dict(email=f"{status}@example.com", status=status),
    )
    assert user.status == status


@pytest.mark.asyncio
async def test_user_invalid_status_raises(db_session: AsyncSession):
    """status is constrained to known values — anything else raises."""
    org, _ = await make_org_with_owner(
        db_session, user_kwargs=dict(email="owner-badstatus@example.com")
    )
    user = make_user(organization_id=org.id, email="badstatus@example.com", status="potato")
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Cascade ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organization_deletion_cascades_to_member_users(db_session: AsyncSession):
    """Deleting an org removes every member User row — owner and non-owner."""
    org, owner = await make_org_with_owner(
        db_session, user_kwargs=dict(email="owner@example.com")
    )
    member = make_user(
        organization_id=org.id, email="member@example.com", role_name="employee"
    )
    db_session.add(member)
    await db_session.flush()
    owner_id, member_id = owner.id, member.id

    await db_session.delete(org)
    await db_session.flush()

    result = await db_session.execute(
        select(User).where(User.id.in_([owner_id, member_id]))
    )
    assert result.scalars().all() == []


# ════════════════════════════════════════════════
# UserSettings
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_settings_defaults(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="settings@example.com")
    )
    settings = make_settings(user.id, org.id)
    db_session.add(settings)
    await db_session.flush()

    assert settings.organization_id == org.id
    assert settings.email_notifications is True
    assert settings.push_notifications is True
    assert settings.challenge_reminders is True
    assert settings.public_profile is False
    assert settings.show_wellness_to_team is False
    assert settings.theme == "light"
    assert settings.two_fa_enabled is False
    assert settings.two_fa_method is None
    assert settings.two_fa_secret is None


@pytest.mark.asyncio
async def test_settings_organization_id_required(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="settings-required@example.com")
    )
    settings = UserSettings(user_id=user.id, organization_id=None)
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_one_per_user(db_session: AsyncSession):
    """unique=True on user_id enforces 1-to-1."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="unique@example.com")
    )
    db_session.add(make_settings(user.id, org.id))
    await db_session.flush()

    db_session.add(make_settings(user.id, org.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_toggle_notifications(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="notif@example.com")
    )
    settings = make_settings(
        user.id, org.id, email_notifications=False, push_notifications=False
    )
    db_session.add(settings)
    await db_session.flush()

    assert settings.email_notifications is False
    assert settings.push_notifications is False


@pytest.mark.asyncio
async def test_settings_dark_mode(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="dark@example.com")
    )
    settings = make_settings(user.id, org.id, theme="dark")
    db_session.add(settings)
    await db_session.flush()

    assert settings.theme == "dark"


@pytest.mark.asyncio
async def test_settings_invalid_theme_raises(db_session: AsyncSession):
    """theme is constrained to 'light' | 'dark'."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="theme-bad@example.com")
    )
    settings = make_settings(user.id, org.id, theme="solarized")
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_privacy_toggles(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="privacy@example.com")
    )
    settings = make_settings(
        user.id, org.id, public_profile=True, show_wellness_to_team=True
    )
    db_session.add(settings)
    await db_session.flush()

    assert settings.public_profile is True
    assert settings.show_wellness_to_team is True


# ── 2FA constraints ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_enable_2fa_totp(db_session: AsyncSession):
    """Valid state: 2FA on, method=totp, secret set."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="2fa@example.com")
    )
    settings = make_settings(
        user.id, org.id,
        two_fa_enabled=True,
        two_fa_method="totp",
        two_fa_secret="ENCRYPTED_SECRET_HERE",
    )
    db_session.add(settings)
    await db_session.flush()

    assert settings.two_fa_enabled is True
    assert settings.two_fa_method == "totp"
    assert settings.two_fa_secret == "ENCRYPTED_SECRET_HERE"


@pytest.mark.asyncio
async def test_settings_2fa_enabled_without_method_raises(db_session: AsyncSession):
    """two_fa_enabled=True requires two_fa_method to be set."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="2fa-no-method@example.com")
    )
    settings = make_settings(
        user.id, org.id,
        two_fa_enabled=True,
        two_fa_method=None,  # missing — should raise
    )
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_2fa_method_set_but_disabled_raises(db_session: AsyncSession):
    """two_fa_enabled=False with two_fa_method set is inconsistent."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="2fa-method-disabled@example.com")
    )
    settings = make_settings(
        user.id, org.id,
        two_fa_enabled=False,
        two_fa_method="totp",  # should raise — enabled is False
    )
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_2fa_totp_without_secret_raises(db_session: AsyncSession):
    """two_fa_method='totp' requires two_fa_secret to be set."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="2fa-totp-no-secret@example.com")
    )
    settings = make_settings(
        user.id, org.id,
        two_fa_enabled=True,
        two_fa_method="totp",
        two_fa_secret=None,  # missing — should raise
    )
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_2fa_sms_with_secret_raises(db_session: AsyncSession):
    """two_fa_method='sms' must not have a secret — secret is TOTP-only."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="2fa-sms-secret@example.com")
    )
    settings = make_settings(
        user.id, org.id,
        two_fa_enabled=True,
        two_fa_method="sms",
        two_fa_secret="SHOULD_NOT_BE_HERE",  # should raise
    )
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_2fa_invalid_method_raises(db_session: AsyncSession):
    """two_fa_method is constrained to totp | sms | email."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="2fa-bad-method@example.com")
    )
    settings = make_settings(
        user.id, org.id,
        two_fa_enabled=True,
        two_fa_method="carrier_pigeon",  # not a valid method
    )
    db_session.add(settings)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Cross-org mismatch (UserSettings) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_org_mismatch_allowed_at_db_level(db_session: AsyncSession):
    """The DB cannot enforce that UserSettings.organization_id matches
    User.organization_id — that is an app-layer invariant. This test
    documents the intentional gap so future developers don't assume a
    constraint exists."""
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )
    user = make_user(org_a.id, email="mismatch-settings@example.com")
    db_session.add(user)
    await db_session.flush()

    # UserSettings.organization_id = org_b but user belongs to org_a
    settings = make_settings(user.id, org_b.id)
    db_session.add(settings)
    await db_session.flush()  # must NOT raise — service layer owns this check

    assert settings.organization_id == org_b.id


# ── UserSettings org-scoped query ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_org_scoped_query(db_session: AsyncSession):
    """organization_id enables direct org-wide queries without a JOIN."""
    org, owner = await make_org_with_owner(
        db_session, user_kwargs=dict(email="settings-scope-owner@example.com")
    )
    member = make_user(
        organization_id=org.id,
        email="settings-scope-member@example.com",
        role_name="employee",
    )
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_settings(owner.id, org.id, two_fa_enabled=False))
    db_session.add(make_settings(member.id, org.id, two_fa_enabled=False))
    await db_session.flush()

    result = await db_session.execute(
        select(UserSettings).where(UserSettings.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


# ════════════════════════════════════════════════
# UserAuthProvider
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_auth_provider_password(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="pwd@example.com")
    )
    provider = make_auth_provider(user.id, org.id, provider="password")
    db_session.add(provider)
    await db_session.flush()

    assert provider.provider == "password"
    assert provider.provider_user_id is None
    assert provider.access_token is None


@pytest.mark.asyncio
async def test_auth_provider_organization_id_required(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="auth-required@example.com")
    )
    provider = UserAuthProvider(user_id=user.id, provider="password", organization_id=None)
    db_session.add(provider)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_auth_provider_google(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="google@example.com")
    )
    provider = make_auth_provider(
        user.id, org.id,
        provider="google",
        provider_user_id="google-sub-12345",
        access_token="ENCRYPTED_ACCESS_TOKEN",
        refresh_token="ENCRYPTED_REFRESH_TOKEN",
    )
    db_session.add(provider)
    await db_session.flush()

    assert provider.provider == "google"
    assert provider.provider_user_id == "google-sub-12345"


@pytest.mark.asyncio
async def test_auth_provider_multiple_providers_same_user(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="multi@example.com")
    )
    db_session.add(make_auth_provider(user.id, org.id, provider="password"))
    db_session.add(
        make_auth_provider(
            user.id, org.id, provider="google", provider_user_id="g-123"
        )
    )
    await db_session.flush()

    result = await db_session.execute(
        select(UserAuthProvider).where(UserAuthProvider.user_id == user.id)
    )
    providers = result.scalars().all()
    assert len(providers) == 2
    assert {p.provider for p in providers} == {"password", "google"}


@pytest.mark.asyncio
async def test_auth_provider_duplicate_raises(db_session: AsyncSession):
    """Same user + same provider blocked by unique index."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="dup_provider@example.com")
    )
    db_session.add(
        make_auth_provider(user.id, org.id, provider="google", provider_user_id="g-1")
    )
    await db_session.flush()

    db_session.add(
        make_auth_provider(user.id, org.id, provider="google", provider_user_id="g-2")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Provider integrity constraints ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_provider_oauth_without_provider_user_id_raises(db_session: AsyncSession):
    """OAuth provider must supply a provider_user_id (sub claim)."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="oauth-no-sub@example.com")
    )
    provider = make_auth_provider(
        user.id, org.id,
        provider="google",
        provider_user_id=None,  # missing — should raise
    )
    db_session.add(provider)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_auth_provider_password_with_provider_user_id_raises(db_session: AsyncSession):
    """Password auth must not have a provider_user_id."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="pwd-sub@example.com")
    )
    provider = make_auth_provider(
        user.id, org.id,
        provider="password",
        provider_user_id="should-not-exist",  # should raise
    )
    db_session.add(provider)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_auth_provider_password_with_access_token_raises(db_session: AsyncSession):
    """Password rows must not store OAuth tokens."""
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="pwd-token@example.com")
    )
    provider = make_auth_provider(
        user.id, org.id,
        provider="password",
        access_token="SHOULD_NOT_BE_HERE",  # should raise
    )
    db_session.add(provider)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── Cross-org mismatch (UserAuthProvider) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_provider_org_mismatch_allowed_at_db_level(db_session: AsyncSession):
    """Same documented gap as UserSettings — DB allows mismatched
    organization_id; service layer must prevent it."""
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )
    user = make_user(org_a.id, email="mismatch-auth@example.com")
    db_session.add(user)
    await db_session.flush()

    provider = make_auth_provider(user.id, org_b.id, provider="password")
    db_session.add(provider)
    await db_session.flush()  # must NOT raise

    assert provider.organization_id == org_b.id


# ── Org-scoped query ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_providers_org_scoped_query(db_session: AsyncSession):
    org, owner = await make_org_with_owner(
        db_session, user_kwargs=dict(email="org-scope-owner@example.com")
    )
    member = make_user(
        organization_id=org.id,
        email="org-scope-member@example.com",
        role_name="employee",
    )
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_auth_provider(owner.id, org.id, provider="password"))
    db_session.add(
        make_auth_provider(member.id, org.id, provider="google", provider_user_id="g-9")
    )
    await db_session.flush()

    result = await db_session.execute(
        select(UserAuthProvider).where(UserAuthProvider.organization_id == org.id)
    )
    assert len(result.scalars().all()) == 2


# ════════════════════════════════════════════════
# UserDevice
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_device_persists(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="device@example.com")
    )
    device = make_device(user.id, org.id)
    db_session.add(device)
    await db_session.flush()

    assert device.organization_id == org.id
    assert device.device_name == "Chrome on macOS"
    assert device.revoked is False
    assert device.last_seen_at is not None


@pytest.mark.asyncio
async def test_device_organization_id_required(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="device-required@example.com")
    )
    device = UserDevice(user_id=user.id, organization_id=None)
    db_session.add(device)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_device_revoke(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="revoke@example.com")
    )
    device = make_device(user.id, org.id)
    db_session.add(device)
    await db_session.flush()

    device.revoked = True
    await db_session.flush()

    result = await db_session.execute(
        select(UserDevice).where(UserDevice.id == device.id)
    )
    assert result.scalar_one().revoked is True


@pytest.mark.asyncio
async def test_user_multiple_devices(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="multidevice@example.com")
    )
    db_session.add(make_device(user.id, org.id, device_name="Chrome on macOS"))
    db_session.add(make_device(user.id, org.id, device_name="iPhone 15"))
    db_session.add(make_device(user.id, org.id, device_name="Firefox on Windows"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserDevice).where(
            UserDevice.user_id == user.id, UserDevice.revoked == False
        )
    )
    assert len(result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_device_optional_fields_nullable(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="mindevice@example.com")
    )
    device = UserDevice(user_id=user.id, organization_id=org.id)
    db_session.add(device)
    await db_session.flush()

    assert device.device_name is None
    assert device.user_agent is None
    assert device.device_fingerprint is None
    assert device.location_label is None


# ── Cross-org mismatch (UserDevice) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_device_org_mismatch_allowed_at_db_level(db_session: AsyncSession):
    """Same documented gap as UserSettings and UserAuthProvider."""
    org_a, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-a", "business_name": "Org A"},
        user_kwargs={"email": "owner_a@example.com"},
    )
    org_b, _ = await make_org_with_owner(
        db_session,
        org_kwargs={"slug": "org-b", "business_name": "Org B"},
        user_kwargs={"email": "owner_b@example.com"},
    )
    user = make_user(org_a.id, email="mismatch-device@example.com")
    db_session.add(user)
    await db_session.flush()

    device = make_device(user.id, org_b.id)
    db_session.add(device)
    await db_session.flush()  # must NOT raise

    assert device.organization_id == org_b.id


# ── Org-scoped query ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_devices_org_scoped_query(db_session: AsyncSession):
    org, owner = await make_org_with_owner(
        db_session, user_kwargs=dict(email="org-scope-device-owner@example.com")
    )
    member = make_user(
        organization_id=org.id,
        email="org-scope-device-member@example.com",
        role_name="employee",
    )
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_device(owner.id, org.id, device_name="Owner laptop"))
    db_session.add(make_device(member.id, org.id, device_name="Member phone"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserDevice).where(
            UserDevice.organization_id == org.id, UserDevice.revoked == False
        )
    )
    assert len(result.scalars().all()) == 2


# ════════════════════════════════════════════════
# Relationships
# ════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_user_settings_relationship(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="rel_settings@example.com")
    )
    db_session.add(make_settings(user.id, org.id))
    await db_session.flush()
    await db_session.refresh(user, ["settings"])

    assert user.settings is not None
    assert user.settings.user_id == user.id


@pytest.mark.asyncio
async def test_organization_all_user_settings_relationship(db_session: AsyncSession):
    org, owner = await make_org_with_owner(
        db_session, user_kwargs=dict(email="rel-org-settings-owner@example.com")
    )
    member = make_user(
        organization_id=org.id,
        email="rel-org-settings-member@example.com",
        role_name="employee",
    )
    db_session.add(member)
    await db_session.flush()

    db_session.add(make_settings(owner.id, org.id))
    db_session.add(make_settings(member.id, org.id))
    await db_session.flush()
    await db_session.refresh(org, ["all_user_settings"])

    assert len(org.all_user_settings) == 2
    assert {s.user_id for s in org.all_user_settings} == {owner.id, member.id}


@pytest.mark.asyncio
async def test_user_devices_relationship(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="rel_devices@example.com")
    )
    db_session.add(make_device(user.id, org.id, device_name="Chrome on macOS"))
    db_session.add(make_device(user.id, org.id, device_name="iPhone 15"))
    await db_session.flush()
    await db_session.refresh(user, ["devices"])

    assert len(user.devices) == 2


@pytest.mark.asyncio
async def test_user_auth_providers_relationship(db_session: AsyncSession):
    org, user = await make_org_with_owner(
        db_session, user_kwargs=dict(email="rel_auth@example.com")
    )
    db_session.add(make_auth_provider(user.id, org.id, provider="password"))
    db_session.add(
        make_auth_provider(user.id, org.id, provider="google", provider_user_id="g-123")
    )
    await db_session.flush()
    await db_session.refresh(user, ["auth_providers"])

    assert len(user.auth_providers) == 2


@pytest.mark.asyncio
async def test_user_delete_cascades_to_children(db_session: AsyncSession):
    """Deleting a non-owner user cascades to settings, devices, and auth
    providers via user_id FK."""
    org, owner = await make_org_with_owner(
        db_session, user_kwargs=dict(email="owner-for-cascade@example.com")
    )
    user = make_user(
        organization_id=org.id, email="cascade@example.com", role_name="employee"
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_settings(user.id, org.id))
    db_session.add(make_device(user.id, org.id))
    db_session.add(make_auth_provider(user.id, org.id, provider="password"))
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    assert (
        await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(UserDevice).where(UserDevice.user_id == user.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(UserAuthProvider).where(UserAuthProvider.user_id == user.id)
        )
    ).scalar_one_or_none() is None