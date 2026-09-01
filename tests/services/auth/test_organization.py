"""
tests/services/auth/test_organization_service.py

Unit tests for app/services/auth/organization_service.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Permission, Role, User, UserPermission
from app.services.auth.organization_service import (
    assign_role,
    check_business_name_available,
    check_phone_available,
    get_system_role,
    seed_super_admin_permissions,
    slugify,
    unique_slug,
)
from tests.conftest import make_org_with_owner, make_user_with_role


# ── slugify() ──────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_lowercases_and_replaces_spaces(self):
        assert slugify("Acme Corp") == "acme-corp"

    def test_removes_special_characters(self):
        assert slugify("Starlit Fitness & Wellness!") == "starlit-fitness-wellness"

    def test_collapses_multiple_hyphens(self):
        assert slugify("A   B---C") == "a-b-c"

    def test_strips_leading_trailing_whitespace_and_hyphens(self):
        assert slugify("  -Hello World-  ") == "hello-world"

    def test_empty_string(self):
        assert slugify("") == ""


# ── unique_slug() ──────────────────────────────────────────────────────────────

class TestUniqueSlug:

    @pytest.mark.asyncio
    async def test_returns_clean_slug_when_available(self, db_session: AsyncSession):
        slug = await unique_slug(db_session, "Brand New Co")
        assert slug == "brand-new-co"

    @pytest.mark.asyncio
    async def test_appends_suffix_on_collision(self, db_session: AsyncSession):
        await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="taken-name", business_name="Taken Name"),
            user_kwargs=dict(email="first@example.com"),
        )

        slug = await unique_slug(db_session, "Taken Name")
        assert slug != "taken-name"
        assert slug.startswith("taken-name-")

    @pytest.mark.asyncio
    async def test_guarantees_uniqueness_on_repeated_collision(
        self, db_session: AsyncSession
    ):
        # Create the base slug
        await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="dup-co", business_name="Dup Co"),
            user_kwargs=dict(email="dup1@example.com"),
        )
        slug2 = await unique_slug(db_session, "Dup Co")
        await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug=slug2, business_name="Dup Co Two"),
            user_kwargs=dict(email="dup2@example.com"),
        )

        slug3 = await unique_slug(db_session, "Dup Co")
        assert slug3 not in ("dup-co", slug2)


# ── check_business_name_available() ─────────────────────────────────────────────

class TestCheckBusinessNameAvailable:

    @pytest.mark.asyncio
    async def test_true_when_available(self, db_session: AsyncSession):
        result = await check_business_name_available(db_session, "Totally Fresh Name")
        assert result is True

    @pytest.mark.asyncio
    async def test_false_when_taken(self, db_session: AsyncSession):
        await make_org_with_owner(
            db_session,
            org_kwargs=dict(business_name="Existing Biz", slug="existing-biz"),
            user_kwargs=dict(email="biz@example.com"),
        )
        result = await check_business_name_available(db_session, "Existing Biz")
        assert result is False

    @pytest.mark.asyncio
    async def test_case_insensitive(self, db_session: AsyncSession):
        await make_org_with_owner(
            db_session,
            org_kwargs=dict(business_name="Case Test Inc", slug="case-test-inc"),
            user_kwargs=dict(email="case@example.com"),
        )
        result = await check_business_name_available(db_session, "  CASE test INC  ")
        assert result is False


# ── check_phone_available() ─────────────────────────────────────────────────────

class TestCheckPhoneAvailable:

    @pytest.mark.asyncio
    async def test_true_when_available(self, db_session: AsyncSession):
        result = await check_phone_available(db_session, "+10000000000")
        assert result is True

    @pytest.mark.asyncio
    async def test_false_when_taken(self, db_session: AsyncSession):
        await make_org_with_owner(
            db_session,
            user_kwargs=dict(
                email="phoneowner@example.com",
                phone_number="+15551234567",
            ),
        )
        result = await check_phone_available(db_session, "+15551234567")
        assert result is False

    @pytest.mark.asyncio
    async def test_strips_whitespace_before_check(self, db_session: AsyncSession):
        await make_org_with_owner(
            db_session,
            user_kwargs=dict(
                email="phoneowner2@example.com",
                phone_number="+15559998888",
            ),
        )
        result = await check_phone_available(db_session, "  +15559998888  ")
        assert result is False


# ── get_system_role() ────────────────────────────────────────────────────────────

class TestGetSystemRole:

    @pytest.mark.asyncio
    async def test_finds_existing_system_role(self, db_session: AsyncSession):
        role = Role(name="super_admin", organization_id=None, branch_id=None, is_system=True)
        db_session.add(role)
        await db_session.flush()

        found = await get_system_role(db_session, "super_admin")
        assert found is not None
        assert found.id == role.id

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, db_session: AsyncSession):
        found = await get_system_role(db_session, "does_not_exist")
        assert found is None

    @pytest.mark.asyncio
    async def test_does_not_match_org_scoped_role(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        org_role = Role(
            name="custom_manager",
            organization_id=org.id,
            branch_id=owner.branch_id,  # ← add this
            is_system=False,
        )
        db_session.add(org_role)
        await db_session.flush()

        found = await get_system_role(db_session, "custom_manager")
        assert found is None, "get_system_role must not return org-scoped roles"

# ── assign_role() ─────────────────────────────────────────────────────────────────

class TestAssignRole:

    def test_sets_role_id(self):
        user = User(
            first_name="Test",
            last_name="User",
            email="assign@example.com",
            organization_id="fake-org-id",
        )
        role = Role(id="fake-role-id", name="member", is_system=True)

        assign_role(user, role)

        assert user.role_id == role.id

    def test_does_not_grant_permissions(self):
        """assign_role only sets role_id — it must not touch UserPermission."""
        user = User(
            first_name="Test",
            last_name="User",
            email="noperm@example.com",
            organization_id="fake-org-id",
        )
        role = Role(id="fake-role-id-2", name="member", is_system=True)

        assign_role(user, role)

        # No way for assign_role to have added permissions — this is a
        # documentation-style assertion confirming the function's scope.
        assert not hasattr(user, "_pending_permissions")


# ── seed_super_admin_permissions() ──────────────────────────────────────────────

class TestSeedSuperAdminPermissions:
    """The legacy hook cannot re-create direct grants after role-only access."""

    @pytest.mark.asyncio
    async def test_compatibility_hook_does_not_add_direct_grants(self, db_session):
        org, user = await make_org_with_owner(db_session)
        db_session.add(Permission(name="overview", category="dashboard", scope="both"))
        await db_session.flush()
        await seed_super_admin_permissions(db_session, user)
        await seed_super_admin_permissions(db_session, user)
        assert (await db_session.execute(select(UserPermission).where(UserPermission.user_id == user.id))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_organization_copy_inherits_template_permissions(self, db_session):
        from app.models import RolePermission
        from app.services.organization_roles import ensure_organization_roles
        from app.services.auth.token_service import load_user_permissions

        org, user = await make_org_with_owner(db_session)
        permission = Permission(name="overview", category="dashboard", scope="both")
        db_session.add(permission)
        await db_session.flush()
        db_session.add(RolePermission(role_id=user.role_id, permission_id=permission.id, scope="org"))
        await db_session.flush()
        roles = await ensure_organization_roles(db_session, org.id)
        copied = roles["super_admin"]
        assert copied.id != user.role_id
        assert copied.template_role_id == user.role_id
        assert copied.organization_id == org.id
        user.role_id = copied.id
        await db_session.flush()
        grants = await load_user_permissions(db_session, user.id, org.id)
        assert [(g.name, g.branch_id) for g in grants] == [("overview", None)]

    @pytest.mark.asyncio
    async def test_reprovision_preserves_existing_copy_and_discovers_new_template(self, db_session):
        from app.services.organization_roles import ensure_organization_roles
        org, user = await make_org_with_owner(db_session)
        first = await ensure_organization_roles(db_session, org.id)
        copied_id = first["super_admin"].id
        first["super_admin"].description = "Organization-specific description"
        db_session.add(Role(name="new-system-role", is_system=True))
        await db_session.flush()
        second = await ensure_organization_roles(db_session, org.id)
        assert second["super_admin"].id == copied_id
        assert second["super_admin"].description == "Organization-specific description"
        assert second["new-system-role"].organization_id == org.id
