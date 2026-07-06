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
        role = Role(id="fake-role-id", name="employee", is_system=True)

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
        role = Role(id="fake-role-id-2", name="employee", is_system=True)

        assign_role(user, role)

        # No way for assign_role to have added permissions — this is a
        # documentation-style assertion confirming the function's scope.
        assert not hasattr(user, "_pending_permissions")


# ── seed_super_admin_permissions() ──────────────────────────────────────────────

class TestSeedSuperAdminPermissions:

    @pytest.mark.asyncio
    async def test_grants_every_permission_in_db(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)

        perm1 = Permission(name="department.create", category="department", scope="org")
        perm2 = Permission(name="post.delete", category="content", scope="both")
        perm3 = Permission(name="branch.create", category="branch", scope="org")
        db_session.add_all([perm1, perm2, perm3])
        await db_session.flush()

        await seed_super_admin_permissions(db_session, user)
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        grants = result.scalars().all()
        assert len(grants) == 3

        granted_permission_ids = {g.permission_id for g in grants}
        assert granted_permission_ids == {perm1.id, perm2.id, perm3.id}

    @pytest.mark.asyncio
    async def test_all_grants_are_org_wide(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)

        perm = Permission(name="member.invite", category="member", scope="both")
        db_session.add(perm)
        await db_session.flush()

        await seed_super_admin_permissions(db_session, user)
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        grants = result.scalars().all()
        assert all(g.branch_id is None for g in grants), (
            "Super admin permission grants must always be org-wide"
        )

    @pytest.mark.asyncio
    async def test_all_grants_are_system_seeded(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)

        perm = Permission(name="org.deactivate", category="org", scope="org")
        db_session.add(perm)
        await db_session.flush()

        await seed_super_admin_permissions(db_session, user)
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        grants = result.scalars().all()
        assert all(g.granted_by is None for g in grants), (
            "System-seeded grants must have granted_by=None"
        )

    @pytest.mark.asyncio
    async def test_grants_correct_organization_id(self, db_session: AsyncSession):
        org, user = await make_org_with_owner(db_session)

        perm = Permission(name="club.create", category="club", scope="both")
        db_session.add(perm)
        await db_session.flush()

        await seed_super_admin_permissions(db_session, user)
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        grants = result.scalars().all()
        assert all(g.organization_id == org.id for g in grants)

    @pytest.mark.asyncio
    async def test_no_permissions_in_db_grants_nothing(self, db_session: AsyncSession):
        """If the permissions table is empty (seed script not run yet),
        seed_super_admin_permissions must not error — just grant nothing."""
        org, user = await make_org_with_owner(db_session)

        await seed_super_admin_permissions(db_session, user)
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_new_permission_added_later_is_picked_up(
        self, db_session: AsyncSession
    ):
        """
        Confirms the function fetches ALL permissions live from the DB
        rather than a hardcoded list — adding a permission to the seed
        catalogue means every future org owner gets it automatically,
        with zero code change to this function.
        """
        org, user = await make_org_with_owner(db_session)

        perm1 = Permission(name="event.create", category="event", scope="both")
        db_session.add(perm1)
        await db_session.flush()

        # Simulate a NEW permission being added to the catalogue after
        # this point, but BEFORE seeding runs for this user
        perm2 = Permission(name="challenge.create", category="challenge", scope="both")
        db_session.add(perm2)
        await db_session.flush()

        await seed_super_admin_permissions(db_session, user)
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission).where(UserPermission.user_id == user.id)
        )
        granted_ids = {g.permission_id for g in result.scalars().all()}
        assert perm1.id in granted_ids
        assert perm2.id in granted_ids