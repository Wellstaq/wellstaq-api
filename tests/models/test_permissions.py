"""
Tests for Permission, Role, UserRoleAssignment, and UserPermission models.

REVISED for the branch/role redesign:
  - The org-wide custom role tier is GONE. Every custom Role now
    requires BOTH organization_id AND branch_id — there is no more
    "organization_id set, branch_id NULL" custom role. The old
    TestOrgWideRole class tested exactly that tier and has been
    replaced with TestCustomRoleRequiresBranch, which asserts the
    opposite: attempting to create a branch-less custom role now
    raises IntegrityError.
  - Added TestRoleUniqueness — is_unique/unique_scope had no coverage
    before. unique_scope only accepts 'branch'/'department'/NULL now
    (no 'org').
  - Added TestUserRoleAssignment — the new table tracking WHERE a
    user's single role is exercised, had zero coverage before this.
  - Every User created beyond the org owner now needs branch_id
    explicitly (NOT NULL) — added `branch_id=owner.branch_id`
    everywhere a second user is created without a specific branch
    being the point of the test.

Covers:
  - Permission CRUD and uniqueness
  - Role scoping: system, branch-scoped custom (org-wide tier removed)
  - Role partial-index uniqueness constraints
  - Role CheckConstraints (system/org/branch consistency, unique_scope)
  - UserRoleAssignment: XOR scope constraint, row-level dedupe, cascades
  - UserPermission direct grants (org-wide and branch-scoped)
  - UserPermission partial-index uniqueness constraints
  - UserPermission CheckConstraint (branch-requires-org)
  - Permission check query pattern (org-wide and branch-scoped inheritance)
  - CASCADE deletes (org deleted -> custom roles gone, branch deleted -> branch-scoped grants gone)
"""

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Permission, Role, UserPermission
from app.models.permission import UserRoleAssignment
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


async def _make_branch_role(db_session, org, branch, **overrides) -> Role:
    defaults = dict(name="branch_manager", organization_id=org.id, branch_id=branch.id, is_system=False)
    role = Role(**{**defaults, **overrides})
    db_session.add(role)
    await db_session.flush()
    return role


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
# Role — custom roles now REQUIRE a branch (org-wide tier removed)
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomRoleRequiresBranch:
    """
    REPLACES the old TestOrgWideRole class. There is no more
    "organization_id set, branch_id NULL" custom role tier — every
    custom role must have BOTH set. These tests assert that boundary
    directly, since it's the single biggest behavior change to Role.
    """

    @pytest.mark.asyncio
    async def test_custom_role_without_branch_raises(self, db_session):
        """The old 'org-wide custom role' shape (branch_id=None) is no
        longer valid — ck_role_system_org_consistent now requires
        branch_id to be set for any custom role."""
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "org-role-1", "business_name": "OrgRole1"}
        )
        with pytest.raises(IntegrityError):
            bad = Role(name="hr_manager", organization_id=org.id, branch_id=None, is_system=False)
            db_session.add(bad)
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_custom_role_with_org_but_no_branch_and_no_org_also_raises(self, db_session):
        """Belt-and-suspenders: neither org nor branch set, is_system=False."""
        with pytest.raises(IntegrityError):
            bad = Role(name="ghost_role_2", organization_id=None, branch_id=None, is_system=False)
            db_session.add(bad)
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_custom_role_with_branch_but_no_org_raises(self, db_session):
        """branch_id set but organization_id missing also violates the
        combined invariant (previously a separate constraint; now
        folded into ck_role_system_org_consistent)."""
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

    @pytest.mark.asyncio
    async def test_valid_custom_role_requires_both_org_and_branch(self, db_session):
        """The only valid custom-role shape now: both set together."""
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "org-role-valid", "business_name": "OrgRoleValid"}
        )
        branch = await _make_branch(db_session, org)
        role = Role(name="hr_manager", organization_id=org.id, branch_id=branch.id, is_system=False)
        db_session.add(role)
        await db_session.flush()
        assert role.id is not None
        assert role.branch_id == branch.id


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
    async def test_same_name_allowed_as_system_and_branch_scoped(self, db_session):
        """
        REPLACES test_same_name_allowed_as_org_wide_and_branch_scoped
        (which relied on the now-removed org-wide custom tier). A
        system role and a branch-scoped custom role can share a name
        because system roles have organization_id=NULL and sit outside
        both partial indexes entirely (organization_id IS NOT NULL is
        implicit in both).
        """
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "br-role-4", "business_name": "BrRole4"}
        )
        branch = await _make_branch(db_session, org)
        await get_or_create_system_role(db_session, "leader")
        db_session.add(Role(name="leader", organization_id=org.id, branch_id=branch.id, is_system=False))
        await db_session.flush()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Role — is_unique / unique_scope (NEW coverage — didn't exist before)
# ─────────────────────────────────────────────────────────────────────────────

class TestRoleUniqueness:

    @pytest.mark.asyncio
    async def test_unique_scope_branch_is_valid(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "uniq-1", "business_name": "Uniq1"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(
            db_session, org, branch, name="branch_manager", is_unique=True, unique_scope="branch"
        )
        assert role.is_unique is True
        assert role.unique_scope == "branch"

    @pytest.mark.asyncio
    async def test_unique_scope_department_is_valid(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "uniq-2", "business_name": "Uniq2"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(
            db_session, org, branch, name="team_leader", is_unique=True, unique_scope="department"
        )
        assert role.unique_scope == "department"

    @pytest.mark.asyncio
    async def test_unique_scope_org_is_no_longer_valid(self, db_session):
        """'org' was a valid unique_scope before this redesign; it no
        longer is — there is no org-wide scope tier left to anchor it
        to. Attempting it must raise."""
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "uniq-3", "business_name": "Uniq3"}
        )
        branch = await _make_branch(db_session, org)
        with pytest.raises(IntegrityError):
            await _make_branch_role(
                db_session, org, branch, name="bad_scope", is_unique=True, unique_scope="org"
            )

    @pytest.mark.asyncio
    async def test_is_unique_true_requires_unique_scope_set(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "uniq-4", "business_name": "Uniq4"}
        )
        branch = await _make_branch(db_session, org)
        with pytest.raises(IntegrityError):
            await _make_branch_role(
                db_session, org, branch, name="inconsistent", is_unique=True, unique_scope=None
            )

    @pytest.mark.asyncio
    async def test_is_unique_false_requires_unique_scope_null(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "uniq-5", "business_name": "Uniq5"}
        )
        branch = await _make_branch(db_session, org)
        with pytest.raises(IntegrityError):
            await _make_branch_role(
                db_session, org, branch, name="inconsistent2", is_unique=False, unique_scope="branch"
            )

    @pytest.mark.asyncio
    async def test_default_is_not_unique(self, db_session):
        org, _ = await make_org_with_owner(
            db_session, org_kwargs={"slug": "uniq-6", "business_name": "Uniq6"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(db_session, org, branch, name="member_like")
        assert role.is_unique is False
        assert role.unique_scope is None


# ─────────────────────────────────────────────────────────────────────────────
# UserRoleAssignment (NEW model — zero coverage before this)
# ─────────────────────────────────────────────────────────────────────────────

class TestUserRoleAssignment:

    @pytest.mark.asyncio
    async def test_create_branch_scoped_assignment(self, db_session):
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-1", "business_name": "URA1"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(
            db_session, org, branch, name="branch_manager", is_unique=True, unique_scope="branch"
        )

        assignment = UserRoleAssignment(
            user_id=owner.id,
            role_id=role.id,
            organization_id=org.id,
            branch_id=branch.id,
            department_id=None,
        )
        db_session.add(assignment)
        await db_session.flush()
        assert assignment.id is not None
        assert assignment.department_id is None

    @pytest.mark.asyncio
    async def test_create_department_scoped_assignment(self, db_session):
        from app.models.department import Department

        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-2", "business_name": "URA2"}
        )
        branch = await _make_branch(db_session, org)
        dept = Department(organization_id=org.id, branch_id=branch.id, name="Engineering")
        db_session.add(dept)
        await db_session.flush()
        role = await _make_branch_role(
            db_session, org, branch, name="team_leader", is_unique=True, unique_scope="department"
        )

        assignment = UserRoleAssignment(
            user_id=owner.id,
            role_id=role.id,
            organization_id=org.id,
            branch_id=None,
            department_id=dept.id,
        )
        db_session.add(assignment)
        await db_session.flush()
        assert assignment.branch_id is None
        assert assignment.department_id == dept.id

    @pytest.mark.asyncio
    async def test_neither_branch_nor_department_raises(self, db_session):
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-3", "business_name": "URA3"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(db_session, org, branch)

        with pytest.raises(IntegrityError):
            db_session.add(UserRoleAssignment(
                user_id=owner.id, role_id=role.id, organization_id=org.id,
                branch_id=None, department_id=None,
            ))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_both_branch_and_department_raises(self, db_session):
        from app.models.department import Department

        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-4", "business_name": "URA4"}
        )
        branch = await _make_branch(db_session, org)
        dept = Department(organization_id=org.id, branch_id=branch.id, name="Sales")
        db_session.add(dept)
        await db_session.flush()
        role = await _make_branch_role(db_session, org, branch)

        with pytest.raises(IntegrityError):
            db_session.add(UserRoleAssignment(
                user_id=owner.id, role_id=role.id, organization_id=org.id,
                branch_id=branch.id, department_id=dept.id,
            ))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_same_user_can_hold_same_role_in_two_branches(self, db_session):
        """The core scenario this table exists for: one role, two
        different branches, same user — two rows, not a conflict."""
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-5", "business_name": "URA5"}
        )
        branch_a = await _make_branch(db_session, org)
        branch_b = Branch(organization_id=org.id, name="Second Branch")
        db_session.add(branch_b)
        await db_session.flush()
        role = await _make_branch_role(
            db_session, org, branch_a, name="branch_manager", is_unique=True, unique_scope="branch"
        )

        db_session.add(UserRoleAssignment(
            user_id=owner.id, role_id=role.id, organization_id=org.id, branch_id=branch_a.id,
        ))
        db_session.add(UserRoleAssignment(
            user_id=owner.id, role_id=role.id, organization_id=org.id, branch_id=branch_b.id,
        ))
        await db_session.flush()  # must not raise — different branch_id each row

    @pytest.mark.asyncio
    async def test_duplicate_exact_row_raises(self, db_session):
        """Same user + same role + same branch twice is a literal
        duplicate row, blocked by uq_user_role_assignment_no_duplicate_row
        — distinct from the is_unique business rule, which is a
        service-layer check across DIFFERENT users."""
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-6", "business_name": "URA6"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(db_session, org, branch)

        db_session.add(UserRoleAssignment(
            user_id=owner.id, role_id=role.id, organization_id=org.id, branch_id=branch.id,
        ))
        await db_session.flush()

        with pytest.raises(IntegrityError):
            db_session.add(UserRoleAssignment(
                user_id=owner.id, role_id=role.id, organization_id=org.id, branch_id=branch.id,
            ))
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_deleting_user_cascades_assignment(self, db_session):
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-7", "business_name": "URA7"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(db_session, org, branch)
        member = await make_user_with_role(
            db_session, organization_id=org.id, email="ura7-member@example.com",
            branch_id=owner.branch_id,
        )
        db_session.add(member)
        await db_session.flush()

        assignment = UserRoleAssignment(
            user_id=member.id, role_id=role.id, organization_id=org.id, branch_id=branch.id,
        )
        db_session.add(assignment)
        await db_session.flush()
        assignment_id = assignment.id

        await db_session.delete(member)
        await db_session.flush()

        result = await db_session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.id == assignment_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_deleting_branch_cascades_assignment_but_not_home_branch(self, db_session):
        """Deleting a branch removes UserRoleAssignment rows scoped to
        it, but must NOT be confused with User.branch_id (home) — a
        user's home branch is a separate relationship entirely."""
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-8", "business_name": "URA8"}
        )
        oversight_branch = await _make_branch(db_session, org)
        role = await _make_branch_role(db_session, org, oversight_branch)

        assignment = UserRoleAssignment(
            user_id=owner.id, role_id=role.id, organization_id=org.id, branch_id=oversight_branch.id,
        )
        db_session.add(assignment)
        await db_session.flush()
        assignment_id = assignment.id
        owner_home_branch_id = owner.branch_id

        await db_session.delete(oversight_branch)
        await db_session.flush()

        result = await db_session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.id == assignment_id)
        )
        assert result.scalar_one_or_none() is None

        # Owner's home branch (HQ, from make_org_with_owner) is untouched.
        await db_session.refresh(owner)
        assert owner.branch_id == owner_home_branch_id

    @pytest.mark.asyncio
    async def test_deleting_role_cascades_assignment(self, db_session):
        org, owner = await make_org_with_owner(
            db_session, org_kwargs={"slug": "ura-9", "business_name": "URA9"}
        )
        branch = await _make_branch(db_session, org)
        role = await _make_branch_role(db_session, org, branch)

        assignment = UserRoleAssignment(
            user_id=owner.id, role_id=role.id, organization_id=org.id, branch_id=branch.id,
        )
        db_session.add(assignment)
        await db_session.flush()
        assignment_id = assignment.id

        await db_session.delete(role)
        await db_session.flush()

        result = await db_session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.id == assignment_id)
        )
        assert result.scalar_one_or_none() is None


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
            db_session, organization_id=org.id, email="member.ow3@example.com",
            branch_id=owner.branch_id,
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
        """Same (user, permission) can exist org-wide AND branch-scoped
        — different index partitions. NOTE: this is about UserPermission
        (direct permission grants), which still has an org-wide tier —
        that's unchanged. Only Role's custom-role tier lost org-wide."""
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
        org, owner = await make_org_with_owner(
            db_session,
            org_kwargs={"slug": "pcheck-2", "business_name": "PCheck2"},
        )
        perm = await _make_permission(db_session, name="user.invite.chk2")
        branch = await _make_branch(db_session, org)

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
        b1 = Branch(organization_id=org.id, name="HQ2")
        b2 = Branch(organization_id=org.id, name="Remote")
        db_session.add_all([b1, b2])
        await db_session.flush()

        db_session.add(UserPermission(
            user_id=owner.id, permission_id=perm.id, organization_id=org.id, branch_id=b1.id
        ))
        await db_session.flush()

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
            db_session, organization_id=org.id, email="member.gb1@example.com",
            branch_id=owner.branch_id,
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