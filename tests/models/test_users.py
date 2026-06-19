"""
User, UserSettings, UserAuthProvider, UserDevice tests.

All tests are async, isolated per test via rollback in conftest.
No HTTP client needed here — these are model/ORM-level tests.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User, UserSettings, UserAuthProvider, UserDevice

# ── Factories ─────────────────────────────────────────────────────────────────

def make_user(**overrides) -> User:
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        role_name="super_admin",
        hashed_password="$2b$12$fakehash",
    )
    return User(**{**defaults, **overrides})


def make_settings(user_id: str, **overrides) -> UserSettings:
    return UserSettings(user_id=user_id, **overrides)


def make_auth_provider(user_id: str, provider="password", **overrides) -> UserAuthProvider:
    return UserAuthProvider(user_id=user_id, provider=provider, **overrides)


def make_device(user_id: str, **overrides) -> UserDevice:
    defaults = dict(device_name="Chrome on macOS", ip_address="127.0.0.1")
    return UserDevice(user_id=user_id, **{**defaults, **overrides})


# ── User model ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_persists(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == user.id))
    fetched = result.scalar_one()
    assert fetched.email == "ada@example.com"
    assert fetched.first_name == "Ada"


@pytest.mark.asyncio
async def test_user_id_is_uuid_format(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    parts = user.id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


@pytest.mark.asyncio
async def test_user_timestamps_auto_set(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    assert user.created_at is not None
    assert user.updated_at is not None


@pytest.mark.asyncio
async def test_user_default_status_is_pending(db_session: AsyncSession):
    user = make_user()
    db_session.add(user)
    await db_session.flush()
    assert user.status == "pending"


@pytest.mark.asyncio
async def test_user_duplicate_email_raises(db_session: AsyncSession):
    db_session.add(make_user(email="dup@example.com"))
    await db_session.flush()

    db_session.add(make_user(email="dup@example.com", first_name="Other"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_optional_fields_nullable(db_session: AsyncSession):
    user = make_user(email="min@example.com")
    db_session.add(user)
    await db_session.flush()

    # organization_id, branch_id, invite_id omitted — columns not yet active
    assert user.phone_number is None
    assert user.avatar_url is None
    assert user.country is None
    assert user.state is None


@pytest.mark.asyncio
async def test_user_soft_delete(db_session: AsyncSession):
    user = make_user(email="gone@example.com")
    db_session.add(user)
    await db_session.flush()

    deleter = make_user(email="boss@example.com", role_name="super_admin")
    db_session.add(deleter)
    await db_session.flush()

    user.deleted_at = datetime.now(timezone.utc)
    user.deleted_by = deleter.id
    await db_session.flush()

    result = await db_session.execute(select(User).where(User.id == user.id))
    fetched = result.scalar_one()
    assert fetched.deleted_at is not None
    assert fetched.deleted_by == deleter.id


@pytest.mark.asyncio
async def test_user_deleted_at_null_by_default(db_session: AsyncSession):
    user = make_user(email="alive@example.com")
    db_session.add(user)
    await db_session.flush()
    assert user.deleted_at is None
    assert user.deleted_by is None


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["super_admin", "branch_manager", "team_lead", "employee", "custom_role"])
async def test_user_accepts_any_role_name(db_session: AsyncSession, role: str):
    """role_name is a free string — no enum constraint."""
    user = make_user(email=f"{role}@example.com", role_name=role)
    db_session.add(user)
    await db_session.flush()
    assert user.role_name == role


# ── UserSettings ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_settings_defaults(db_session: AsyncSession):
    user = make_user(email="settings@example.com")
    db_session.add(user)
    await db_session.flush()

    settings = make_settings(user.id)
    db_session.add(settings)
    await db_session.flush()

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
async def test_settings_toggle_notifications(db_session: AsyncSession):
    user = make_user(email="notif@example.com")
    db_session.add(user)
    await db_session.flush()

    settings = make_settings(user.id, email_notifications=False, push_notifications=False)
    db_session.add(settings)
    await db_session.flush()

    assert settings.email_notifications is False
    assert settings.push_notifications is False


@pytest.mark.asyncio
async def test_settings_dark_mode(db_session: AsyncSession):
    user = make_user(email="dark@example.com")
    db_session.add(user)
    await db_session.flush()

    settings = make_settings(user.id, theme="dark")
    db_session.add(settings)
    await db_session.flush()

    assert settings.theme == "dark"


@pytest.mark.asyncio
async def test_settings_enable_2fa(db_session: AsyncSession):
    user = make_user(email="2fa@example.com")
    db_session.add(user)
    await db_session.flush()

    settings = make_settings(
        user.id,
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
async def test_settings_one_per_user(db_session: AsyncSession):
    """unique=True on user_id enforces 1-to-1."""
    user = make_user(email="unique@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_settings(user.id))
    await db_session.flush()

    db_session.add(make_settings(user.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_settings_privacy_toggles(db_session: AsyncSession):
    user = make_user(email="privacy@example.com")
    db_session.add(user)
    await db_session.flush()

    settings = make_settings(user.id, public_profile=True, show_wellness_to_team=True)
    db_session.add(settings)
    await db_session.flush()

    assert settings.public_profile is True
    assert settings.show_wellness_to_team is True


# ── UserAuthProvider ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_provider_password(db_session: AsyncSession):
    user = make_user(email="pwd@example.com")
    db_session.add(user)
    await db_session.flush()

    provider = make_auth_provider(user.id, provider="password")
    db_session.add(provider)
    await db_session.flush()

    assert provider.provider == "password"
    assert provider.provider_user_id is None
    assert provider.access_token is None


@pytest.mark.asyncio
async def test_auth_provider_google(db_session: AsyncSession):
    user = make_user(email="google@example.com")
    db_session.add(user)
    await db_session.flush()

    provider = make_auth_provider(
        user.id,
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
    """One user can have both password and Google linked."""
    user = make_user(email="multi@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_auth_provider(user.id, provider="password"))
    db_session.add(make_auth_provider(user.id, provider="google", provider_user_id="g-123"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserAuthProvider).where(UserAuthProvider.user_id == user.id)
    )
    providers = result.scalars().all()
    assert len(providers) == 2
    assert {p.provider for p in providers} == {"password", "google"}


@pytest.mark.asyncio
async def test_auth_provider_duplicate_raises(db_session: AsyncSession):
    """Same user + same provider is blocked by unique index."""
    user = make_user(email="dup_provider@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_auth_provider(user.id, provider="google", provider_user_id="g-1"))
    await db_session.flush()

    db_session.add(make_auth_provider(user.id, provider="google", provider_user_id="g-2"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── UserDevice ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_device_persists(db_session: AsyncSession):
    user = make_user(email="device@example.com")
    db_session.add(user)
    await db_session.flush()

    device = make_device(user.id)
    db_session.add(device)
    await db_session.flush()

    assert device.device_name == "Chrome on macOS"
    assert device.revoked is False
    assert device.last_seen_at is not None


@pytest.mark.asyncio
async def test_device_revoke(db_session: AsyncSession):
    user = make_user(email="revoke@example.com")
    db_session.add(user)
    await db_session.flush()

    device = make_device(user.id)
    db_session.add(device)
    await db_session.flush()

    device.revoked = True
    await db_session.flush()

    result = await db_session.execute(select(UserDevice).where(UserDevice.id == device.id))
    fetched = result.scalar_one()
    assert fetched.revoked is True


@pytest.mark.asyncio
async def test_user_multiple_devices(db_session: AsyncSession):
    user = make_user(email="multidevice@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_device(user.id, device_name="Chrome on macOS"))
    db_session.add(make_device(user.id, device_name="iPhone 15"))
    db_session.add(make_device(user.id, device_name="Firefox on Windows"))
    await db_session.flush()

    result = await db_session.execute(
        select(UserDevice).where(UserDevice.user_id == user.id, UserDevice.revoked == False)
    )
    devices = result.scalars().all()
    assert len(devices) == 3


@pytest.mark.asyncio
async def test_device_optional_fields_nullable(db_session: AsyncSession):
    user = make_user(email="mindevice@example.com")
    db_session.add(user)
    await db_session.flush()

    device = UserDevice(user_id=user.id)
    db_session.add(device)
    await db_session.flush()

    assert device.device_name is None
    assert device.user_agent is None
    assert device.device_fingerprint is None
    assert device.location_label is None


# ── Relationships ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_settings_relationship(db_session: AsyncSession):
    user = make_user(email="rel_settings@example.com")
    db_session.add(user)
    await db_session.flush()

    settings = make_settings(user.id)
    db_session.add(settings)
    await db_session.flush()
    await db_session.refresh(user, ["settings"])

    assert user.settings is not None
    assert user.settings.user_id == user.id


@pytest.mark.asyncio
async def test_user_devices_relationship(db_session: AsyncSession):
    user = make_user(email="rel_devices@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_device(user.id, device_name="Chrome on macOS"))
    db_session.add(make_device(user.id, device_name="iPhone 15"))
    await db_session.flush()
    await db_session.refresh(user, ["devices"])

    assert len(user.devices) == 2


@pytest.mark.asyncio
async def test_user_auth_providers_relationship(db_session: AsyncSession):
    user = make_user(email="rel_auth@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_auth_provider(user.id, provider="password"))
    db_session.add(make_auth_provider(user.id, provider="google", provider_user_id="g-123"))
    await db_session.flush()
    await db_session.refresh(user, ["auth_providers"])

    assert len(user.auth_providers) == 2


@pytest.mark.asyncio
async def test_user_delete_cascades_to_children(db_session: AsyncSession):
    """Deleting a user should cascade to settings, devices, and auth providers."""
    user = make_user(email="cascade@example.com")
    db_session.add(user)
    await db_session.flush()

    db_session.add(make_settings(user.id))
    db_session.add(make_device(user.id))
    db_session.add(make_auth_provider(user.id, provider="password"))
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    assert (await db_session.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none() is None
    assert (await db_session.execute(select(UserDevice).where(UserDevice.user_id == user.id))).scalar_one_or_none() is None
    assert (await db_session.execute(select(UserAuthProvider).where(UserAuthProvider.user_id == user.id))).scalar_one_or_none() is None