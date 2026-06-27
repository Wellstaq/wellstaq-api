"""
Permission model — role and permission are SEPARATE and UNRELATED.

Design decisions:
  - Role is just a label/grouping (system or custom). It does NOT
    carry permissions. Assigning a role to a user does not grant them
    anything by itself.
  - Permission is the atomic "can do X" capability, platform-seeded
    only. Orgs never create, rename, or delete permissions.
  - A user has exactly ONE role (role_id directly on users table).
  - A user can have MANY permissions, assigned directly and
    independently of their role, via UserPermission.
  - There is no RolePermission join table. Role and Permission do not
    reference each other at all. Granting/revoking a permission to a
    user is a separate action from assigning/changing their role.

Scope model (applies to BOTH Role and UserPermission):
  - Every assignment is scoped to either the whole organization OR a
    specific branch within that organization.
  - organization_id is always required — it anchors the tenant boundary
    and prevents cross-org leakage.
  - branch_id is optional:
      NULL  → org-wide  — the assignment applies across the whole org.
      SET   → branch-scoped — the assignment applies only within that
              branch. branch_id must belong to organization_id; the DB
              cannot enforce this cross-table, so the SERVICE LAYER MUST
              validate it on every insert/update.

  Examples:
      "Can invite users across the whole org"
          → UserPermission(org_id=X, branch_id=NULL, permission="user.invite")
      "Can invite users only within Branch Lagos"
          → UserPermission(org_id=X, branch_id=B, permission="user.invite")
      "Holds 'Branch Manager' role in Branch Lagos only"
          → Role(org_id=X, branch_id=B, name="branch_manager")
          → User.role_id = that role's id

Permission check pattern (service layer):
    -- Org-wide check:
    SELECT 1 FROM user_permissions up
    JOIN permissions p ON p.id = up.permission_id
    WHERE up.user_id = :user_id
      AND up.organization_id = :org_id
      AND up.branch_id IS NULL          -- org-wide grant
      AND p.name = :permission_name
    LIMIT 1;

    -- Branch-scoped check (also inherits org-wide grants):
    SELECT 1 FROM user_permissions up
    JOIN permissions p ON p.id = up.permission_id
    WHERE up.user_id = :user_id
      AND up.organization_id = :org_id
      AND (up.branch_id = :branch_id OR up.branch_id IS NULL)
      AND p.name = :permission_name
    LIMIT 1;
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
    Atomic capability the platform knows about.

    Seeded by the platform via a migration or seed script. Orgs cannot
    create, rename, or delete permissions. name follows
    "resource.action" convention and is unique platform-wide. category
    groups related permissions for UI display.

    Examples:
        post.delete, post.moderate
        user.suspend, user.elevate
        user.invite
        event.create, event.cancel
        wellbeing.view_team, wellbeing.export

    Granted to users directly via UserPermission — has NO relationship
    to Role.
    """

    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    user_permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Permission id={self.id} name={self.name}>"


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A label assigned to a user — system, org-wide custom, or
    branch-scoped custom. Purely descriptive. Carries NO permissions
    of its own and has no relationship to Permission or UserPermission.

    Scope tiers:
      1. System roles   (is_system=True,  organization_id=NULL, branch_id=NULL)
            Platform defaults, read-only, shared across all orgs.
            e.g. "super_admin", "member"

      2. Org-wide roles (is_system=False, organization_id=SET,  branch_id=NULL)
            Created by an org admin, apply across the whole org.
            Cascade-deleted when the org is deleted.
            e.g. "hr_manager", "auditor"

      3. Branch-scoped roles (is_system=False, organization_id=SET, branch_id=SET)
            Created by an org admin or branch manager; apply only within
            a specific branch. Cascade-deleted when the branch is deleted.
            branch_id MUST belong to organization_id — enforced at the
            service layer (DB cannot cross-row check).
            e.g. "branch_manager", "branch_coordinator"

    Assigning this role to a user does not grant or imply any
    permissions. Permissions are granted separately and directly to
    the user via UserPermission.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL  = system/platform role
    # SET   = org-wide or branch-scoped custom role
    organization_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # NULL  = org-wide (or system if org is also NULL)
    # SET   = branch-scoped; must belong to organization_id (service layer enforced)
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # True = platform default, read-only. False = org/branch-created, mutable.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    
    # Realationships

    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="custom_roles",
        foreign_keys=[organization_id],
    )
    invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="invited_role"
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="role",
        foreign_keys=[branch_id],
    )

    # Users currently holding this role (a user has exactly one role,
    # but a role can be held by many users)
    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="role",
        foreign_keys="User.role_id",
    )

    __table_args__ = (
        # Role name uniqueness, split by scope tier.
        #
        # A single UniqueConstraint("name", "organization_id", "branch_id")
        # looks right but does NOT work for org-wide or branch-scoped
        # custom roles, because SQLite (and Postgres) treat two NULLs as
        # distinct values inside a unique index — so two org-wide roles
        # (branch_id=NULL) with the same name in the same org would NOT
        # be caught. Splitting into partial unique indexes, one per
        # "branch_id is/isn't NULL" case, closes that gap:
        #
        #   - org-wide roles  (organization_id SET, branch_id NULL):
        #       unique by (name, organization_id)
        #   - branch-scoped roles (branch_id SET):
        #       unique by (name, organization_id, branch_id)
        #
        # System roles (organization_id IS NULL) are deliberately NOT
        # covered by either index — there's no organization_id to scope
        # uniqueness by, so duplicate system-role names are a
        # service-layer responsibility, not a DB-level one.
        Index(
            "uq_role_name_org_no_branch",
            "name", "organization_id",
            unique=True,
            sqlite_where=text("branch_id IS NULL AND organization_id IS NOT NULL"),
            postgresql_where=text("branch_id IS NULL AND organization_id IS NOT NULL"),
        ),
        Index(
            "uq_role_name_org_branch",
            "name", "organization_id", "branch_id",
            unique=True,
            sqlite_where=text("branch_id IS NOT NULL"),
            postgresql_where=text("branch_id IS NOT NULL"),
        ),

        # Invariant 1: system flag and org/branch columns must be consistent.
        #   system  → org NULL,  branch NULL
        #   custom  → org SET,   branch NULL or SET
        CheckConstraint(
            "(is_system = 1 AND organization_id IS NULL AND branch_id IS NULL) "
            "OR (is_system = 0 AND organization_id IS NOT NULL)",
            name="ck_role_system_org_consistent",
        ),

        # Invariant 2: branch_id may only be set when organization_id is also set.
        CheckConstraint(
            "branch_id IS NULL OR organization_id IS NOT NULL",
            name="ck_role_branch_requires_org",
        ),
    )
    def __repr__(self) -> str:
        if self.is_system:
            scope = "system"
        elif self.branch_id:
            scope = f"branch={self.branch_id}"
        else:
            scope = f"org={self.organization_id}"
        return f"<Role id={self.id} name={self.name} scope={scope}>"


class UserPermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Direct grant of a permission to a user, completely independent
    of whatever role that user holds.

    Scope:
      organization_id is ALWAYS required — anchors the tenant boundary.
      branch_id is OPTIONAL:
        NULL → org-wide grant  — permission applies everywhere in the org.
        SET  → branch-scoped   — permission applies only within that branch.

      The service layer MUST:
        1. Validate that the granting user is acting within organization_id.
        2. Validate that branch_id (when set) belongs to organization_id.
        3. When checking a permission for a branch-scoped action, match
           rows where branch_id = :branch_id OR branch_id IS NULL, so
           org-wide grants are automatically inherited at the branch level.

    Unique on (user_id, permission_id, organization_id, branch_id).
    branch_id IS NULL is treated as a distinct value in the unique key,
    so a user can hold the same permission both org-wide and branch-scoped
    without conflict (though the org-wide grant makes the branch grant
    redundant — the service layer may warn about this).

    granted_by tracks who made the grant (audit trail); nullable for
    system-seeded grants.
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

    # Tenant boundary — always required.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Scope discriminator.
    # NULL  → org-wide:      permission applies across the whole org.
    # SET   → branch-scoped: permission applies only within this branch.
    #         Must belong to organization_id — service layer enforced.
    #         CASCADE on branch delete — when a branch is removed, all
    #         branch-scoped permission grants for it are removed too.
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

    # ── Relationships ──────────────────────────────────────────────────────
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
        # Uniqueness on (user, permission, org, branch-scope), split into
        # two partial indexes for the same NULL-comparison reason as Role
        # above. organization_id is always required here, so only the
        # branch_id split is needed:
        #
        #   - org-wide grants     (branch_id IS NULL):
        #       unique by (user_id, permission_id, organization_id)
        #   - branch-scoped grants (branch_id IS NOT NULL):
        #       unique by (user_id, permission_id, organization_id, branch_id)
        #
        # This still allows a user to hold the same permission both
        # org-wide AND branch-scoped at once — they land in different
        # indexes, so no conflict (see class docstring: the org-wide
        # grant makes the branch one redundant but not invalid).
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

        # branch_id may only be set when organization_id is set.
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