"""
tests/services/auth/test_token_service.py

Unit tests for app/services/token_service.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token, hash_refresh_token
from app.core.constants import TokenType
from app.models import Permission, Role, User, UserDevice, UserPermission
from app.services.auth.token_service import (
    _load_role_name,
    access_only_response,
    build_claims,
    issue_tokens,
    load_user_permissions,
    revoke_device,
    rotate_refresh_token,
    token_pair_response,
    two_fa_challenge_response,
)
from tests.conftest import make_org_with_owner, make_user_with_role


# ── load_user_permissions() ─────────────────────────────────────────────────────

class TestLoadUserPermissions:

    @pytest.mark.asyncio
    async def test_returns_granted_permission_names(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        perm = Permission(name="department.create", category="department", scope="org")
        db_session.add(perm)
        await db_session.flush()

        db_session.add(UserPermission(
            user_id=user.id,
            permission_id=perm.id,
            organization_id=org.id,
            branch_id=None,
        ))
        await db_session.flush()

        names = await load_user_permissions(db_session, user.id, org.id)
        assert names == ["department.create"]

    @pytest.mark.asyncio
    async def test_does_not_return_permissions_from_other_org(
        self, db_session: AsyncSession
    ):
        org1, user1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="u1@example.com"),
        )
        org2, user2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="u2@example.com"),
        )

        perm = Permission(name="post.delete", category="content", scope="both")
        db_session.add(perm)
        await db_session.flush()

        db_session.add(UserPermission(
            user_id=user1.id,
            permission_id=perm.id,
            organization_id=org1.id,
            branch_id=None,
        ))
        await db_session.flush()

        names = await load_user_permissions(db_session, user1.id, org2.id)
        assert names == []

    @pytest.mark.asyncio
    async def test_no_duplicates(self, db_session: AsyncSession):
        """
        A user can hold the same permission org-wide AND branch-scoped
        simultaneously (two UserPermission rows) — load_user_permissions
        must still return the name only once.
        """
        from app.models.branch import Branch

        org, user = await make_org_with_owner(db_session)
        branch = Branch(organization_id=org.id, name="Lagos")
        db_session.add(branch)
        await db_session.flush()

        perm = Permission(name="member.suspend", category="member", scope="both")
        db_session.add(perm)
        await db_session.flush()

        db_session.add(UserPermission(
            user_id=user.id, permission_id=perm.id,
            organization_id=org.id, branch_id=None,
        ))
        db_session.add(UserPermission(
            user_id=user.id, permission_id=perm.id,
            organization_id=org.id, branch_id=branch.id,
        ))
        await db_session.flush()

        names = await load_user_permissions(db_session, user.id, org.id)
        assert names == ["member.suspend"]

    @pytest.mark.asyncio
    async def test_empty_when_no_permissions(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        names = await load_user_permissions(db_session, user.id, org.id)
        assert names == []


# ── _load_role_name() ────────────────────────────────────────────────────────────

class TestLoadRoleName:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_role_id(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(
            db_session, user_kwargs=dict(role_id=None, role_name=None)
        ) if False else (None, None)
        # Build a bare user with no role_id directly — avoids
        # make_org_with_owner's automatic role assignment.
        user = User(
            first_name="No", last_name="Role",
            email="norole@example.com",
            organization_id="fake-org-id",
            role_id=None,
        )
        name = await _load_role_name(db_session, user)
        assert name is None

    @pytest.mark.asyncio
    async def test_returns_name_via_db_query_when_not_eager_loaded(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)
        # user.role_id is set via make_org_with_owner, but the relationship
        # may not be eager-loaded in this fresh fetch.
        await db_session.refresh(user)

        name = await _load_role_name(db_session, user)
        assert name == "overall_manager"

    @pytest.mark.asyncio
    async def test_uses_eager_loaded_role_when_present(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        await db_session.refresh(user, ["role"])

        name = await _load_role_name(db_session, user)
        assert name == "overall_manager"


# ── build_claims() ────────────────────────────────────────────────────────────────

class TestBuildClaims:

    @pytest.mark.asyncio
    async def test_returns_all_expected_keys(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        claims = await build_claims(db_session, user)

        assert set(claims.keys()) == {
            "organization_id", "branch_id", "role", "permissions"
        }

    @pytest.mark.asyncio
    async def test_organization_id_matches_user(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        claims = await build_claims(db_session, user)
        assert claims["organization_id"] == org.id

    @pytest.mark.asyncio
    async def test_branch_id_passed_through(self, db_session: AsyncSession):
        from app.models.branch import Branch

        org, user = await make_org_with_owner(db_session)
        branch = Branch(organization_id=org.id, name="Abuja")
        db_session.add(branch)
        await db_session.flush()

        user.branch_id = branch.id
        await db_session.flush()

        claims = await build_claims(db_session, user)
        assert claims["branch_id"] == branch.id

    @pytest.mark.asyncio
    async def test_permissions_match_load_user_permissions(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)
        perm = Permission(name="department.update", category="department", scope="org")
        db_session.add(perm)
        await db_session.flush()

        db_session.add(UserPermission(
            user_id=user.id, permission_id=perm.id,
            organization_id=org.id, branch_id=None,
        ))
        await db_session.flush()

        claims = await build_claims(db_session, user)
        assert claims["permissions"] == ["department.update"]

    @pytest.mark.asyncio
    async def test_role_matches_assigned_role(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        claims = await build_claims(db_session, user)
        assert claims["role"] == "overall_manager"


# ── Response shape helpers ────────────────────────────────────────────────────────

class TestResponseShapes:

    def test_token_pair_response(self):
        result = token_pair_response("access123", "refresh456")
        assert result == {
            "access_token": "access123",
            "refresh_token": "refresh456",
            "token_type": "bearer",
        }

    def test_access_only_response(self):
        result = access_only_response("access123")
        assert result == {
            "access_token": "access123",
            "token_type": "bearer",
        }

    def test_two_fa_challenge_response(self):
        result = two_fa_challenge_response("challenge789", "totp")
        assert result == {
            "requires_2fa": True,
            "two_fa_method": "totp",
            "two_fa_challenge_token": "challenge789",
        }


# ── issue_tokens() ──────────────────────────────────────────────────────────────

class TestIssueTokens:

    @pytest.mark.asyncio
    async def test_returns_token_pair_shape(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)

        tokens = await issue_tokens(db_session, user, "iPhone", "fp-1", "1.2.3.4")

        assert set(tokens.keys()) == {"access_token", "refresh_token", "token_type"}
        assert tokens["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_access_token_decodes_with_correct_claims(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)
        perm = Permission(name="branch.create", category="branch", scope="org")
        db_session.add(perm)
        await db_session.flush()
        db_session.add(UserPermission(
            user_id=user.id, permission_id=perm.id,
            organization_id=org.id, branch_id=None,
        ))
        await db_session.flush()

        tokens = await issue_tokens(db_session, user, "Chrome", "fp-2", "5.6.7.8")
        payload = decode_token(tokens["access_token"])

        assert payload["sub"] == user.id
        assert payload["org_id"] == org.id
        assert payload["role"] == "overall_manager"
        assert "branch.create" in payload["permissions"]
        assert payload["type"] == TokenType.ACCESS

    @pytest.mark.asyncio
    async def test_refresh_token_decodes_with_correct_sub_and_type(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)
        tokens = await issue_tokens(db_session, user, "Firefox", "fp-3", "9.9.9.9")
        payload = decode_token(tokens["refresh_token"])

        assert payload["sub"] == user.id
        assert payload["type"] == TokenType.REFRESH

    @pytest.mark.asyncio
    async def test_creates_new_device_when_no_fingerprint_match(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)
        await issue_tokens(db_session, user, "Safari", "unique-fp", "10.0.0.1")

        result = await db_session.execute(
            select(UserDevice).where(
                UserDevice.user_id == user.id,
                UserDevice.device_fingerprint == "unique-fp",
            )
        )
        device = result.scalar_one_or_none()
        assert device is not None
        assert device.revoked is False
        assert device.device_name == "Safari"

    @pytest.mark.asyncio
    async def test_updates_existing_device_in_place(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)

        # First login
        tokens1 = await issue_tokens(db_session, user, "Safari", "same-fp", "10.0.0.1")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "same-fp")
        )
        device_before = result.scalar_one()
        device_id = device_before.id
        old_hash = device_before.refresh_token_hash

        # Second login, same fingerprint
        tokens2 = await issue_tokens(db_session, user, "Safari", "same-fp", "10.0.0.2")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "same-fp")
        )
        devices = result.scalars().all()

        assert len(devices) == 1, "Should update in place, not create a second row"
        assert devices[0].id == device_id
        assert devices[0].refresh_token_hash != old_hash
        assert devices[0].ip_address == "10.0.0.2"

    @pytest.mark.asyncio
    async def test_revoked_device_with_same_fingerprint_creates_new_row(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)

        await issue_tokens(db_session, user, "Safari", "revoked-fp", "10.0.0.1")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "revoked-fp")
        )
        old_device = result.scalar_one()
        old_device.revoked = True
        await db_session.flush()

        # Login again with the same fingerprint — old device is revoked,
        # so a NEW device row should be created
        await issue_tokens(db_session, user, "Safari", "revoked-fp", "10.0.0.3")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "revoked-fp")
        )
        devices = result.scalars().all()
        assert len(devices) == 2
        active = [d for d in devices if not d.revoked]
        assert len(active) == 1


# ── rotate_refresh_token() ───────────────────────────────────────────────────────

class TestRotateRefreshToken:

    @pytest.mark.asyncio
    async def test_returns_new_token_pair(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        await issue_tokens(db_session, user, "Edge", "rot-fp", "1.1.1.1")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "rot-fp")
        )
        device = result.scalar_one()

        new_tokens = await rotate_refresh_token(db_session, user, device)
        assert set(new_tokens.keys()) == {"access_token", "refresh_token", "token_type"}

    @pytest.mark.asyncio
    async def test_old_refresh_hash_is_invalidated(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        old_tokens = await issue_tokens(db_session, user, "Edge", "rot-fp-2", "1.1.1.2")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "rot-fp-2")
        )
        device = result.scalar_one()
        old_hash = device.refresh_token_hash

        await rotate_refresh_token(db_session, user, device)
        await db_session.refresh(device)

        assert device.refresh_token_hash != old_hash
        # The OLD raw refresh token's hash must no longer match the stored hash
        assert hash_refresh_token(old_tokens["refresh_token"]) != device.refresh_token_hash

    @pytest.mark.asyncio
    async def test_new_refresh_token_hash_matches_stored_hash(
        self, db_session: AsyncSession
    ):
        org, user = await make_org_with_owner(db_session)
        await issue_tokens(db_session, user, "Edge", "rot-fp-3", "1.1.1.3")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "rot-fp-3")
        )
        device = result.scalar_one()

        new_tokens = await rotate_refresh_token(db_session, user, device)
        await db_session.refresh(device)

        assert hash_refresh_token(new_tokens["refresh_token"]) == device.refresh_token_hash


# ── revoke_device() ────────────────────────────────────────────────────────────────

class TestRevokeDevice:

    @pytest.mark.asyncio
    async def test_sets_revoked_true(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        await issue_tokens(db_session, user, "Edge", "rev-fp", "1.1.1.4")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "rev-fp")
        )
        device = result.scalar_one()

        await revoke_device(db_session, device)
        await db_session.refresh(device)

        assert device.revoked is True

    @pytest.mark.asyncio
    async def test_clears_refresh_token_hash(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)
        await issue_tokens(db_session, user, "Edge", "rev-fp-2", "1.1.1.5")

        result = await db_session.execute(
            select(UserDevice).where(UserDevice.device_fingerprint == "rev-fp-2")
        )
        device = result.scalar_one()
        assert device.refresh_token_hash is not None  # sanity check

        await revoke_device(db_session, device)
        await db_session.refresh(device)

        assert device.refresh_token_hash is None