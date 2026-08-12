"""
Permission model — role and permission are SEPARATE and UNRELATED.

Design decisions:
  - Role is just a label/grouping (system or custom). It does NOT
    carry permissions. Assigning a role to a user does not grant them
    anything by itself.
  - Permission is the atomic "can do X" capability, platform-seeded
    only. Orgs never create, rename, or delete permissions.
  - A user has exactly ONE role (role_id directly on users table).
    That role never changes based on WHERE they exercise it — see
    UserRoleAssignment below for that.
  - A user can have MANY permissions, assigned directly and
    independently of their role, via UserPermission.
  - There is no RolePermission join table. Role and Permission do not
    reference each other at all.

Role scope tiers (REVISED — no org-wide tier anymore):
  1. System roles  (is_system=True,  organization_id=NULL, branch_id=NULL)
        Platform defaults, read-only, shared across all orgs.
        e.g. "super_admin", "branch_manager", "team_leader", "member"
        System roles can NEVER be overridden, edited, or deleted by an
        org — not even a copy-and-modify. An org that wants something
        different creates an entirely separate custom Role row; the
        system row itself is immutable at the service layer (the DB
        does not special-case this — it must be enforced in whichever
        service handles role edit/delete).

  2. Custom roles  (is_system=False, organization_id=SET, branch_id=SET)
        Created by an org/branch admin. ALWAYS branch-scoped now — there
        is no org-wide custom tier. An "org-wide" position is simply a
        role assigned at the org's Headquarters branch; there is no
        separate scope tier for it in the data model.
        Isolated per branch: a custom role created in Branch A is not
        visible to or usable by Branch B. If Branch B wants something
        similar, it creates its own Role row — no sharing.
        Cascade-deleted when its branch is deleted.

  There used to be a middle "org-wide custom role" tier
  (organization_id SET, branch_id NULL). That tier is REMOVED. Every
  custom role now requires both organization_id AND branch_id.

Assigning this role to a user (User.role_id) does not by itself say
WHERE the user exercises it — see UserRoleAssignment below, which is
the new table that tracks that.

Role.is_unique / unique_scope:
  Whether a role can be held by more than one person within a given
  branch or department. This is now enforced against
  UserRoleAssignment rows, not against User.role_id directly, because
  one user can hold the SAME role in MULTIPLE branches/departments
  simultaneously (e.g. branch_manager of Branch A AND Branch B) — see
  UserRoleAssignment docstring.

    unique_scope="branch"     → e.g. branch_manager: at most one
                                 active UserRoleAssignment per
                                 (role_id, branch_id).
    unique_scope="department" → e.g. team_leader: at most one active
                                 UserRoleAssignment per
                                 (role_id, department_id).
    unique_scope=NULL         → is_unique=False; no uniqueness
                                 enforced (e.g. "member" — many people,
                                 no oversight context, usually no
                                 UserRoleAssignment row needed at all).

  IMPORTANT — this uniqueness canNOT be enforced as a blanket DB
  constraint on UserRoleAssignment, because a naive
  UNIQUE(role_id, branch_id) index would also block ordinary
  non-unique roles (e.g. two "member" rows in the same branch), which
  is valid and common. Since a role's is_unique/unique_scope is a
  property of the Role row, not of UserRoleAssignment, and SQLite/
  Postgres cannot express a cross-table conditional constraint,
  uniqueness here is a SERVICE LAYER responsibility: before inserting
  a UserRoleAssignment, the service must look up whether the target
  role.is_unique, and if so query for an existing row matching
  (role_id, branch_id) or (role_id, department_id) per unique_scope,
  raising a 409 if one exists. Same established pattern as branch/org
  membership checks elsewhere in this codebase.

Permission.scope field — unchanged from before (still org/branch/both
grant-context advisory for the UI). See UserPermission docstring below.
"""

from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Atomic capability the platform knows about. Unchanged.
    """

    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    scope: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="both",
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('org', 'branch', 'both')",
            name="ck_permission_scope_valid",
        ),
    )

    user_permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission", back_populates="permission", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Permission id={self.id} name={self.name} scope={self.scope}>"


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A system role or an organization-owned custom role.

    See module docstring for the full scope-tier explanation and why
    the org-wide custom tier was removed.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL  = system/platform role
    # SET   = organization-owned custom role
    organization_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Roles are never branch-owned. Permission assignments carry scope.
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Organization roles cloned from a platform template point back to it.
    # NULL means either a platform template or a fully custom org role.
    template_role_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=True, index=True,
    )

    # True = platform default. Read-only — cannot be edited, deleted,
    # or "overridden" by any org. Enforce this in the role-management
    # service layer (no model-level way to block an UPDATE/DELETE).
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Uniqueness columns ─────────────────────────────────────────────────────
    is_unique: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    # "branch" | "department" | NULL. "org" is no longer a valid value —
    # there is no org-wide scope tier left to anchor it to. super_admin
    # (the one truly org-wide role) is handled as a special case at
    # signup, outside of UserRoleAssignment entirely — see signup_service.
    unique_scope: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="custom_roles",
        foreign_keys=[organization_id],
    )
    invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="invited_role",
    )
    # CHANGED: a branch can now have MANY custom roles, not one.
    # back_populates target on Branch is now "roles" (plural).
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="roles",
        foreign_keys=[branch_id],
    )

    # Users currently holding this role as their single label
    # (User.role_id) — unchanged meaning, still exactly one per user.
    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="role",
        foreign_keys="User.role_id",
    )

    # NEW — every place this role is actually being exercised.
    assignments: Mapped[list["UserRoleAssignment"]] = relationship(
        "UserRoleAssignment",
        back_populates="role",
        cascade="all, delete-orphan",
    )
    permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan",
    )
    template: Mapped[Optional["Role"]] = relationship(
        "Role", remote_side="Role.id", foreign_keys=[template_role_id],
    )

    __table_args__ = (
        # ── Name uniqueness ──────────────────────────────────────────────────
        #
        # REMOVED: uq_role_name_org_no_branch (the org-wide-custom-role
        # uniqueness index). There's no org-wide custom tier left, so
        # that index no longer has a valid target — every custom role
        # now has branch_id SET, so only the branch-scoped index below
        # applies.
        Index(
            "uq_role_name_org",
            "name", "organization_id",
            unique=True,
            sqlite_where=text("organization_id IS NOT NULL AND branch_id IS NULL"),
            postgresql_where=text("organization_id IS NOT NULL AND branch_id IS NULL"),
        ),
        Index(
            "uq_role_name_org_branch", "name", "organization_id", "branch_id", unique=True,
            sqlite_where=text("branch_id IS NOT NULL"), postgresql_where=text("branch_id IS NOT NULL"),
        ),
        Index(
            "uq_role_org_branch_template", "organization_id", "branch_id", "template_role_id", unique=True,
            sqlite_where=text("template_role_id IS NOT NULL"), postgresql_where=text("template_role_id IS NOT NULL"),
        ),

        # ── Structural invariants ──────────────────────────────────────────────

        # Invariant 1 (REVISED): system flag and org/branch columns
        # must be consistent.
        #   system  → org NULL,     branch NULL
        #   custom  → org NOT NULL, branch NOT NULL  (both required now —
        #             previously branch_id was optional for custom roles)
        CheckConstraint(
            "(is_system = 1 AND organization_id IS NULL AND branch_id IS NULL) "
            "OR (is_system = 0 AND organization_id IS NOT NULL)",
            name="ck_role_system_org_consistent",
        ),

        # Invariant 2 (REMOVED): "branch_id may only be set when
        # organization_id is set" — now redundant, since Invariant 1
        # already requires both together for custom roles and both
        # NULL for system roles. No longer a separate case to guard.

        # Invariant 3 (REVISED): unique_scope drops 'org' as a valid value.
        CheckConstraint(
            "unique_scope IN ('branch', 'department') OR unique_scope IS NULL",
            name="ck_role_unique_scope_valid",
        ),

        # Invariant 4: unchanged — is_unique and unique_scope must agree.
        CheckConstraint(
            "(is_unique = 0 AND unique_scope IS NULL) "
            "OR (is_unique = 1 AND unique_scope IS NOT NULL)",
            name="ck_role_unique_scope_consistency",
        ),
    )

    def __repr__(self) -> str:
        if self.is_system:
            scope = "system"
        else:
            scope = f"branch={self.branch_id}"
        unique_tag = f" unique_scope={self.unique_scope}" if self.is_unique else ""
        return f"<Role id={self.id} name={self.name} scope={scope}{unique_tag}>"


class RolePermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Permission template attached to a role.

    System-role rows are platform seeded and immutable. ``scope`` describes
    how the template resolves for its holder: ``org`` becomes an org-wide
    grant; ``branch`` resolves to the role/user assignment branch. Custom
    roles are branch-scoped and therefore only accept ``branch`` rows.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    permission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="branch")

    role: Mapped["Role"] = relationship("Role", back_populates="permissions")
    permission: Mapped["Permission"] = relationship("Permission", back_populates="role_permissions")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        CheckConstraint("scope IN ('org', 'branch')", name="ck_role_permission_scope"),
    )


class UserRoleAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Tracks WHERE a user exercises their single role (User.role_id).

    This is what makes "one person, one role, but oversees multiple
    branches/departments" work. A user's role_id never changes based
    on location — but the SAME role can have many assignment rows, one
    per branch or department the user oversees in that capacity.

    Example: Dara holds role_id=team_leader.
      UserRoleAssignment(user=Dara, role=team_leader, department=Sales)
      UserRoleAssignment(user=Dara, role=team_leader, department=Support)
    Dara is team_leader in two departments at once — two rows, same
    role_id, different department_id. Her User.role_id is still just
    "team_leader" — a single label — the rows above are what say where.

    Scope is always EXACTLY ONE of branch_id or department_id — never
    both, never neither. Enforced by ck_user_role_assignment_scope_xor.
    A branch-level position (e.g. branch_manager) sets branch_id and
    leaves department_id NULL. A department-level position (e.g.
    team_leader) sets department_id and leaves branch_id NULL.

    organization_id is denormalized (copied from the branch's or
    department's own organization_id at insert time) — purely for
    "give me every role assignment in org X" queries without a JOIN.
    It is NOT a separate scope boundary; branch_id/department_id are
    the only real boundaries.

    Not every role needs a row here. Roles with unique_scope=NULL
    (e.g. "member" — no oversight context, nothing to disambiguate)
    typically have zero UserRoleAssignment rows; User.role_id alone is
    sufficient. Rows here are specifically for roles where "which
    branch/department does this apply to" is a meaningful question —
    i.e. roles with unique_scope='branch' or 'department', though
    non-unique branch/department-contextual roles could also use this
    table if a future need arises (the schema doesn't forbid it).

    Uniqueness (is_unique roles only) is a SERVICE LAYER check, not a
    DB constraint — see Role.unique_scope docstring for why a blanket
    DB unique index here would incorrectly block ordinary non-unique
    roles.

    Cascade behavior:
      user_id       → CASCADE (deleting a user deletes their assignments)
      role_id       → CASCADE (deleting a role deletes assignments of it —
                       though system roles can never be deleted at all,
                       per the service-layer immutability rule)
      branch_id     → CASCADE (deleting a branch deletes assignments
                       scoped to it — does NOT touch User.branch_id,
                       which is a completely separate column/relationship
                       on User representing home base, not oversight)
      department_id → CASCADE (same reasoning, for departments)
    """

    __tablename__ = "user_role_assignments"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalized — not a scope boundary. See class docstring.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Exactly one of these two is set — see
    # ck_user_role_assignment_scope_xor below.
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    department_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="role_assignments",
        foreign_keys=[user_id],
    )
    role: Mapped["Role"] = relationship(
        "Role",
        back_populates="assignments",
        foreign_keys=[role_id],
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="role_assignments",
        foreign_keys=[branch_id],
    )
    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        back_populates="role_assignments",
        foreign_keys=[department_id],
    )

    __table_args__ = (
        # Exactly one of branch_id / department_id must be set.
        CheckConstraint(
            "(branch_id IS NOT NULL AND department_id IS NULL) "
            "OR (branch_id IS NULL AND department_id IS NOT NULL)",
            name="ck_user_role_assignment_scope_xor",
        ),
        # Prevent literal duplicate rows (same user holding the exact
        # same role in the exact same place twice) — this is NOT the
        # is_unique business-rule check (that's service-layer, applies
        # across DIFFERENT users), just basic row-level de-dup.
        #
        # Split into two partial indexes for the same reason as Role's
        # name uniqueness and UserPermission's grant uniqueness: a
        # plain UniqueConstraint("user_id", "role_id", "branch_id",
        # "department_id") does NOT work here, because branch_id and
        # department_id are NEVER both non-NULL on the same row (see
        # ck_user_role_assignment_scope_xor) — SQL treats NULL != NULL,
        # so two rows sharing (user_id, role_id, branch_id=X,
        # department_id=NULL) would never be flagged as duplicates by
        # a single 4-column constraint. Each row only ever has one of
        # branch_id/department_id set, so only one of these two
        # indexes is ever relevant to a given row — but both must
        # exist to cover both cases.
        Index(
            "uq_user_role_assignment_branch_no_duplicate",
            "user_id", "role_id", "branch_id",
            unique=True,
            sqlite_where=text("branch_id IS NOT NULL"),
            postgresql_where=text("branch_id IS NOT NULL"),
        ),
        Index(
            "uq_user_one_branch_role_per_organization",
            "user_id", "organization_id",
            unique=True,
            sqlite_where=text("branch_id IS NOT NULL"),
            postgresql_where=text("branch_id IS NOT NULL"),
        ),
        Index(
            "uq_user_role_assignment_department_no_duplicate",
            "user_id", "role_id", "department_id",
            unique=True,
            sqlite_where=text("department_id IS NOT NULL"),
            postgresql_where=text("department_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        scope = f"branch={self.branch_id}" if self.branch_id else f"department={self.department_id}"
        return f"<UserRoleAssignment user={self.user_id} role={self.role_id} {scope}>"


class UserPermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Direct grant of a permission to a user, completely independent
    of whatever role that user holds. Unchanged from before.
    """

    __tablename__ = "user_permissions"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    granted_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="user_permissions",
        foreign_keys=[user_id],
    )
    permission: Mapped["Permission"] = relationship(
        "Permission",
        back_populates="user_permissions",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="user_permissions",
        foreign_keys=[organization_id],
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="user_permissions",
        foreign_keys=[branch_id],
    )
    granter: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[granted_by],
    )

    __table_args__ = (
        Index(
            "uq_user_permission_org_no_branch",
            "user_id", "permission_id", "organization_id",
            unique=True,
            sqlite_where=text("branch_id IS NULL"),
            postgresql_where=text("branch_id IS NULL"),
        ),
        Index(
            "uq_user_permission_org_branch",
            "user_id", "permission_id", "organization_id", "branch_id",
            unique=True,
            sqlite_where=text("branch_id IS NOT NULL"),
            postgresql_where=text("branch_id IS NOT NULL"),
        ),
        CheckConstraint(
            "branch_id IS NULL OR organization_id IS NOT NULL",
            name="ck_user_permission_branch_requires_org",
        ),
    )

    def __repr__(self) -> str:
        scope = f"branch={self.branch_id}" if self.branch_id else "org-wide"
        return (
            f"<UserPermission user={self.user_id} "
            f"permission={self.permission_id} scope={scope}>"
        )
