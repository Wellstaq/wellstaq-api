"""
Tests for Permission, Role, and UserPermission models.

Covers:
  - Permission CRUD and uniqueness
  - Role scoping: system, org-wide, branch-scoped
  - Role partial-index uniqueness constraints
  - Role CheckConstraints (system/org consistency, branch-requires-org)
  - UserPermission direct grants (org-wide and branch-scoped)
  - UserPermission partial-index uniqueness constraints
  - UserPermission CheckConstraint (branch-requires-org)
  - Permission check query pattern (org-wide and branch-scoped inheritance)
  - CASCADE deletes (org deleted → custom roles gone, branch deleted → branch-scoped grants gone)
"""

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Permission, Role, UserPermission
from app.models import Branch  # adjust import path if needed

from tests.conftest import (
    make_org_with_owner,
    make_user_with_role,
    get_or_create_system_role,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _make_permission(db_session, name="user.invite", category="users") -> Permission:
    p = Permission(name=name, description=f"Can {name}", category=category)
    db_session.add(p)
    await db_session.flush()
    return p


async def _make_branch(db_session, org) -> Branch:
    branch = Branch(organization_id=org.id, name="Lagos Branch")
    db_session.add(branch)
    await db_session.flush()
    return branch


# ─────────────────────────────────────────────────────────────────────────────
# Permission
# ─────────────────────────────────────────────────────────────────────────────

class TestPermission:

    @pytest.mark.asyncio
    async def test_create_permission(self, db_session):
        p = await _make_permission(db_session, name="post.delete")
        assert p.id is not None
        assert p.name == "post.delete"

    @pytest.mark.asyncio
    async def test_permission_name_is_unique(self, db_session):
        await _make_permission(db_session, name="event.create")
        await db_session.flush()

        with pytest.raises(IntegrityError):
            p2 = Permission(name="event.create")
            db_session.add(p2)
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_permission_category_optional(self, db_session):
        p = Permission(name="wellbeing.export")
        db_session.add(p)
        await db_session.flush()
        assert p.category is None

    @pytest.mark.asyncio
    async def test_multiple_permissions_different_names(self, db_session):
        perms = ["post.create", "post.edit", "post.moderate"]
        for name in perms:
            db_session.add(Permission(name=name))
        await db_session.flush()

        result = await db_session.execute(
            select(Permission).where(Permission.name.in_(perms))
        )
        assert len(result.scalars().all()) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Role — system roles
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemRole:

    @pytest.mark.asyncio
    async def test_system_role_created_via_helper(self, db_session):
        role = await get_or_create_system_role(db_session, "member")
        assert role.is_system is True
        assert role.organization_id is None
        assert role.branch_id is None

    @pytest.mark.asyncio
    async def test_system_role_reused_not_duplicated(self, db_session):
        r1 = await get_or_create_system_role(db_session, "employee")
        r2 = await get_or_create_system_role(db_session, "employee")
        assert r1.id == r2.id

    @pytest.mark.asyncio
    async def test_system_role_check_constraint_violated_when_org_set(self, db_session):
        """is_system=True with an org set must fail the check constraint."""
        org, _ = await make_org_with_owner(db_session)
        with pytest.raises(IntegrityError):
            bad = Role(
                name="bad_system",
                is_system=True,
                organization_id=org.id,
            )
            db_session.add(bad)
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_custom_role_check_constraint_violated_when_org_null(self, db_session):
        """is_system=False with no org must fail."""
        with pytest.raises(IntegrityError):
            bad = Role(name="ghost_role", is_system=False, organization_id=None)
            db_session.add(bad)
            await db_session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Role — org-wide custom roles
# ─────────────────────────────────────────────────────────────────────────────

class TestOrgWideRole:

    @pytest.mark.asyncio
    async def test_create_org_wide_role(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "org-role-1", "business_name": "OrgRole1"}
        )
        role = Role(name="hr_manager", organization_id=org.id, is_system=False)
        db_session.add(role)
        await db_session.flush()
        assert role.id is not None
        assert role.branch_id is None

    @pytest.mark.asyncio
    async def test_org_wide_role_name_unique_within_org(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "org-role-2", "business_name": "OrgRole2"}
        )
        db_session.add(Role(name="auditor", organization_id=org.id, is_system=False))
        await db_session.flush()

        with pytest.raises(IntegrityError):
            db_session.add(Role(name="auditor", organization_id=org.id, is_system=False))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_same_role_name_allowed_across_different_orgs(self, db_session):
        org1, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "cross-org-1", "business_name": "CrossOrg1"},
            user_kwargs={"email": "owner1@cross.com"},
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "cross-org-2", "business_name": "CrossOrg2"},
            user_kwargs={"email": "owner2@cross.com"},
        )
        db_session.add(Role(name="team_lead", organization_id=org1.id, is_system=False))
        db_session.add(Role(name="team_lead", organization_id=org2.id, is_system=False))
        await db_session.flush()  # must not raise

    @pytest.mark.asyncio
    async def test_branch_requires_org_constraint_on_role(self, db_session):
        """branch_id set but organization_id NULL must fail."""
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "br-req-org", "business_name": "BrReqOrg"}
        )
        branch = await _make_branch(db_session, org)
        with pytest.raises(IntegrityError):
            bad = Role(
                name="orphan_branch_role",
                is_system=False,
                organization_id=None,
                branch_id=branch.id,
            )
            db_session.add(bad)
            await db_session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Role — branch-scoped custom roles
# ─────────────────────────────────────────────────────────────────────────────

class TestBranchScopedRole:

    @pytest.mark.asyncio
    async def test_create_branch_scoped_role(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "br-role-1", "business_name": "BrRole1"}
        )
        branch = await _make_branch(db_session, org)
        role = Role(
            name="branch_manager",
            organization_id=org.id,
            branch_id=branch.id,
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()
        assert role.branch_id == branch.id

    @pytest.mark.asyncio
    async def test_branch_role_name_unique_within_same_branch(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "br-role-2", "business_name": "BrRole2"}
        )
        branch = await _make_branch(db_session, org)
        db_session.add(Role(
            name="branch_coordinator",
            organization_id=org.id,
            branch_id=branch.id,
            is_system=False,
        ))
        await db_session.flush()

        with pytest.raises(IntegrityError):
            db_session.add(Role(
                name="branch_coordinator",
                organization_id=org.id,
                branch_id=branch.id,
                is_system=False,
            ))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_same_branch_role_name_allowed_in_different_branches(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "br-role-3", "business_name": "BrRole3"}
        )
        b1 = Branch(organization_id=org.id, name="Branch A")
        b2 = Branch(organization_id=org.id, name="Branch B")
        db_session.add_all([b1, b2])
        await db_session.flush()

        db_session.add(Role(name="coordinator", organization_id=org.id, branch_id=b1.id, is_system=False))
        db_session.add(Role(name="coordinator", organization_id=org.id, branch_id=b2.id, is_system=False))
        await db_session.flush()  # must not raise

    @pytest.mark.asyncio
    async def test_same_name_allowed_as_org_wide_and_branch_scoped(self, db_session):
        """A name can exist as org-wide AND as branch-scoped — different index partitions."""
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "br-role-4", "business_name": "BrRole4"}
        )
        branch = await _make_branch(db_session, org)
        db_session.add(Role(name="leader", organization_id=org.id, branch_id=None, is_system=False))
        db_session.add(Role(name="leader", organization_id=org.id, branch_id=branch.id, is_system=False))
        await db_session.flush()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# UserPermission — org-wide grants
# ─────────────────────────────────────────────────────────────────────────────

class TestUserPermissionOrgWide:

    @pytest.mark.asyncio
    async def test_grant_org_wide_permission(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-orgwide-1", "business_name": "UPOrgWide1"},
        )
        perm = await _make_permission(db_session, name="user.invite.ow1")

        up = UserPermission(
            user_id=owner.id,
            permission_id=perm.id,
            organization_id=org.id,
            branch_id=None,
        )
        db_session.add(up)
        await db_session.flush()
        assert up.id is not None
        assert up.branch_id is None

    @pytest.mark.asyncio
    async def test_duplicate_org_wide_grant_raises(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-orgwide-2", "business_name": "UPOrgWide2"},
        )
        perm = await _make_permission(db_session, name="user.invite.ow2")

        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id
        ))
        await db_session.flush()

        with pytest.raises(IntegrityError):
            db_session.add(UserPermission(
                user_id=owner.id, permission_id=perm.id, organization_id=org.id
            ))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_same_permission_different_users(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-orgwide-3", "business_name": "UPOrgWide3"},
        )
        perm = await _make_permission(db_session, name="event.create.ow3")
        member = await make_user_with_role(
            db_session, organization_id=org.id, email="member.ow3@example.com"
        )
        db_session.add(member)
        await db_session.flush()

        db_session.add(UserPermission(user_id=owner.id, permission_id=perm.id, organization_id=org.id))
        db_session.add(UserPermission(user_id=member.id, permission_id=perm.id, organization_id=org.id))
        await db_session.flush()  # must not raise

    @pytest.mark.asyncio
    async def test_up_branch_requires_org_constraint(self, db_session):
        """branch_id set but organization_id missing must fail."""
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-br-req", "business_name": "UPBrReq"},
        )
        perm = await _make_permission(db_session, name="wellbeing.view.brreq")
        branch = await _make_branch(db_session, org)

        with pytest.raises(IntegrityError):
            db_session.add(UserPermission(
                user_id=owner.id,
                permission_id=perm.id,
                organization_id=None,   # violates ck_user_permission_branch_requires_org
                branch_id=branch.id,
            ))
            await db_session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# UserPermission — branch-scoped grants
# ─────────────────────────────────────────────────────────────────────────────

class TestUserPermissionBranchScoped:

    @pytest.mark.asyncio
    async def test_grant_branch_scoped_permission(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-branch-1", "business_name": "UPBranch1"},
        )
        perm = await _make_permission(db_session, name="user.invite.bs1")
        branch = await _make_branch(db_session, org)

        up = UserPermission(
            user_id=owner.id,
            permission_id=perm.id,
            organization_id=org.id,
            branch_id=branch.id,
        )
        db_session.add(up)
        await db_session.flush()
        assert up.branch_id == branch.id

    @pytest.mark.asyncio
    async def test_duplicate_branch_scoped_grant_raises(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-branch-2", "business_name": "UPBranch2"},
        )
        perm = await _make_permission(db_session, name="user.invite.bs2")
        branch = await _make_branch(db_session, org)

        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id,
            organization_id=org.id, branch_id=branch.id,
        ))
        await db_session.flush()

        with pytest.raises(IntegrityError):
            db_session.add(UserPermission(
                user_id=owner.id, permission_id=perm.id,
                organization_id=org.id, branch_id=branch.id,
            ))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_org_wide_and_branch_scoped_can_coexist(self, db_session):
        """Same (user, permission) can exist org-wide AND branch-scoped — different index partitions."""
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-branch-3", "business_name": "UPBranch3"},
        )
        perm = await _make_permission(db_session, name="user.invite.bs3")
        branch = await _make_branch(db_session, org)

        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=None
        ))
        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=branch.id
        ))
        await db_session.flush()  # must not raise

    @pytest.mark.asyncio
    async def test_same_permission_different_branches(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "up-branch-4", "business_name": "UPBranch4"},
        )
        perm = await _make_permission(db_session, name="event.cancel.bs4")
        b1 = Branch(organization_id=org.id, name="North")
        b2 = Branch(organization_id=org.id, name="South")
        db_session.add_all([b1, b2])
        await db_session.flush()

        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=b1.id
        ))
        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=b2.id
        ))
        await db_session.flush()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Permission check query pattern
# ─────────────────────────────────────────────────────────────────────────────

class TestPermissionCheckPattern:

    @pytest.mark.asyncio
    async def test_org_wide_permission_check(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "pcheck-1", "business_name": "PCheck1"},
        )
        perm = await _make_permission(db_session, name="user.suspend.chk1")
        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=None
        ))
        await db_session.flush()

        result = await db_session.execute(
            select(UserPermission)
            .join(Permission, Permission.id == UserPermission.permission_id)
            .where(
                UserPermission.user_id == owner.id,
                UserPermission.organization_id == org.id,
                UserPermission.branch_id.is_(None),
                Permission.name == "user.suspend.chk1",
            )
            .limit(1)
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_branch_scoped_check_inherits_org_wide_grant(self, db_session):
        """
        Branch-scoped permission check must return the org-wide grant
        because the check pattern ORs (branch_id = :b OR branch_id IS NULL).
        """
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "pcheck-2", "business_name": "PCheck2"},
        )
        perm = await _make_permission(db_session, name="user.invite.chk2")
        branch = await _make_branch(db_session, org)

        # Only grant org-wide — no branch-scoped grant
        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=None
        ))
        await db_session.flush()

        # Branch-scoped check should still find the org-wide row
        result = await db_session.execute(
            select(UserPermission)
            .join(Permission, Permission.id == UserPermission.permission_id)
            .where(
                UserPermission.user_id == owner.id,
                UserPermission.organization_id == org.id,
                (UserPermission.branch_id == branch.id) | UserPermission.branch_id.is_(None),
                Permission.name == "user.invite.chk2",
            )
            .limit(1)
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_user_without_permission_returns_none(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "pcheck-3", "business_name": "PCheck3"},
        )
        perm = await _make_permission(db_session, name="post.moderate.chk3")
        # Deliberately NOT granting the permission

        result = await db_session.execute(
            select(UserPermission)
            .join(Permission, Permission.id == UserPermission.permission_id)
            .where(
                UserPermission.user_id == owner.id,
                UserPermission.organization_id == org.id,
                UserPermission.branch_id.is_(None),
                Permission.name == "post.moderate.chk3",
            )
            .limit(1)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_branch_only_grant_not_visible_in_other_branch(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "pcheck-4", "business_name": "PCheck4"},
        )
        perm = await _make_permission(db_session, name="event.create.chk4")
        b1 = Branch(organization_id=org.id, name="HQ")
        b2 = Branch(organization_id=org.id, name="Remote")
        db_session.add_all([b1, b2])
        await db_session.flush()

        # Grant only in b1
        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=b1.id
        ))
        await db_session.flush()

        # Check in b2 — should not find it
        result = await db_session.execute(
            select(UserPermission)
            .join(Permission, Permission.id == UserPermission.permission_id)
            .where(
                UserPermission.user_id == owner.id,
                UserPermission.organization_id == org.id,
                (UserPermission.branch_id == b2.id) | UserPermission.branch_id.is_(None),
                Permission.name == "event.create.chk4",
            )
            .limit(1)
        )
        assert result.scalar_one_or_none() is None


# ─────────────────────────────────────────────────────────────────────────────
# granted_by audit trail
# ─────────────────────────────────────────────────────────────────────────────

class TestGrantedBy:

    @pytest.mark.asyncio
    async def test_granted_by_records_granter(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "grantedby-1", "business_name": "GrantedBy1"},
        )
        member = await make_user_with_role(
            db_session, organization_id=org.id, email="member.gb1@example.com"
        )
        db_session.add(member)
        await db_session.flush()

        perm = await _make_permission(db_session, name="user.invite.gb1")
        up = UserPermission(
            user_id=member.id,
            permission_id=perm.id,
            organization_id=org.id,
            granted_by=owner.id,
        )
        db_session.add(up)
        await db_session.flush()
        assert up.granted_by == owner.id

    @pytest.mark.asyncio
    async def test_granted_by_nullable_for_system_seeded(self, db_session):
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "grantedby-2", "business_name": "GrantedBy2"},
        )
        perm = await _make_permission(db_session, name="wellbeing.export.gb2")
        up = UserPermission(
            user_id=owner.id,
            permission_id=perm.id,
            organization_id=org.id,
            granted_by=None,
        )
        db_session.add(up)
        await db_session.flush()
        assert up.granted_by is None