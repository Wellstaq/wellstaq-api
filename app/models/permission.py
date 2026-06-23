"""
RBAC permission model — four tables, four layers.

Design decisions (all explicitly confirmed):
  - Platform-wide default roles + org-created custom roles
  - One user can hold multiple roles simultaneously
  - Branch-level scoping lives on the role ASSIGNMENT (user_roles),
    not on the permission itself
  - Platform default roles are read-only (is_system=True); orgs can
    only create new roles, never modify system ones
  - Same role can be assigned to a user at both org-wide scope AND a
    specific branch scope simultaneously (both rows are valid and
    distinct — the unique constraint includes branch_id so NULL and
    a real branch_id are treated as different scopes)

The four layers:

  1. Permission — the atomic "can do X" capability. Platform-seeded,
     never org-created. Organized by category for UI grouping
     (e.g. "posts", "events", "wellbeing"). Name follows
     "resource.action" convention: "post.delete", "event.create",
     "wellbeing.view_team".

  2. Role — a named bundle of permissions. is_system=True rows are
     platform defaults (Super Admin, Branch Manager, Employee, etc.)
     and are read-only. organization_id IS NOT NULL rows are
     org-custom roles, owned by that org, cascaded away when the
     org is deleted.

  3. RolePermission — the join between Role and Permission. Unique
     on (role_id, permission_id). Orgs can't create new permissions,
     but their custom roles can reference any platform permission.

  4. UserRole — the assignment of a role to a user at a specific
     scope:
       - organization_id always set (a role assignment always belongs
         to an org context — even platform roles are assigned within
         the org a user belongs to)
       - branch_id NULL  = role applies org-wide
       - branch_id SET   = role applies only within that branch
     Unique on (user_id, role_id, organization_id, branch_id) so:
       - the same role can be assigned org-wide AND at a branch
         simultaneously (NULL != a real branch_id, so both rows
         co-exist cleanly)
       - but the exact same (user, role, org, branch) triple is
         blocked from being inserted twice

Permission check pattern (service layer):
    SELECT p.name
    FROM user_roles ur
    JOIN role_permissions rp ON rp.role_id = ur.role_id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE ur.user_id = :user_id
      AND ur.organization_id = :org_id
      AND (ur.branch_id = :branch_id OR ur.branch_id IS NULL)
      AND p.name = :permission_name
    LIMIT 1

role_name on User:
    Kept as a display/UI label only. Access-control decisions MUST use
    user_roles + role_permissions, never role_name. role_name can be
    populated from the user's primary role name for display purposes
    but is not authoritative. This avoids a breaking migration while
    the RBAC tables are being wired up in the service layer.
"""

from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    Computed,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Atomic capability the platform knows about.

    Seeded by the platform via a migration or seed script. Orgs cannot
    create, rename, or delete permissions — they only assign them to
    custom roles. name follows "resource.action" convention and must be
    unique platform-wide. category groups related permissions for UI
    display (e.g. all "post.*" permissions under a "Posts" section).

    Examples:
        post.create, post.delete, post.moderate
        event.create, event.cancel
        wellbeing.view_team, wellbeing.export
        branch.manage, department.manage
        user.invite, user.suspend
        challenge.create, challenge.complete
    """

    __tablename__ = "permissions"

    # "resource.action" — unique, immutable after creation
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    # Human-readable label for UI display
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Groups related permissions in the UI (e.g. "posts", "events",
    # "wellbeing", "admin"). Not enforced by DB — app-layer grouping only.
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    # Relationships
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Permission id={self.id} name={self.name}>"


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Named bundle of permissions.

    Two kinds of roles:
      - System roles (is_system=True, organization_id=NULL): platform
        defaults seeded at deploy time. Read-only — no org may modify,
        rename, or delete them. The service layer must enforce this;
        the DB enforces it via a CHECK constraint that prevents any row
        with is_system=True from having a non-NULL organization_id.
        Examples: "Super Admin", "Branch Manager", "Employee".

      - Org-custom roles (is_system=False, organization_id SET): created
        by the org to model their own hierarchy. Cascade-deleted when
        the org is deleted. Example: "Acme Wellness Lead".

    Orgs can't modify system roles. They can create new roles that
    reference any platform permission in RolePermission.

    name uniqueness: unique within the org (or platform-wide for system
    roles). Enforced via unique index on (name, organization_id) — since
    organization_id IS NULL for system roles, SQLite and Postgres both
    treat NULL as distinct from any value, meaning two NULL rows with
    the same name would NOT be blocked by a standard unique index. To
    prevent duplicate system role names, the service layer must check
    for existing system role names before inserting. A partial unique
    index (WHERE organization_id IS NULL) would enforce this at the DB
    level in Postgres but is not supported by SQLite. Documented here
    as a known limitation of the SQLite test environment.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL = system/platform role; SET = org-custom role
    organization_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # True = platform default, read-only. False = org-created, mutable.
    # CHECK constraint enforces is_system=True => organization_id IS NULL
    # (a system role is never owned by a specific org) and
    # is_system=False => organization_id IS NOT NULL
    # (a custom role must belong to an org).
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="custom_roles",
        foreign_keys=[organization_id],
    )
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
    )
    user_roles: Mapped[list["UserRole"]] = relationship(
        "UserRole",
        back_populates="role",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Unique name within an org (NULL org_id = system roles).
        # See docstring for the NULL-uniqueness caveat on SQLite.
        UniqueConstraint("name", "organization_id", name="uq_role_name_org"),
        # System roles must not belong to an org; custom roles must.
        CheckConstraint(
            "(is_system = 1 AND organization_id IS NULL) "
            "OR (is_system = 0 AND organization_id IS NOT NULL)",
            name="ck_role_system_org_consistent",
        ),
    )

    def __repr__(self) -> str:
        scope = "system" if self.is_system else f"org={self.organization_id}"
        return f"<Role id={self.id} name={self.name} scope={scope}>"


class RolePermission(Base, UUIDPrimaryKeyMixin):
    """
    Many-to-many join between Role and Permission.

    Which permissions a given role grants. Unique on (role_id,
    permission_id) — a permission can only be granted once per role.
    No TimestampMixin needed — these rows are structural config, not
    audit records. No organization_id — the role already carries the
    org context; this table is just the edge between role and
    permission.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relationships
    role: Mapped["Role"] = relationship("Role", back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship(
        "Permission", back_populates="role_permissions"
    )

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )

    def __repr__(self) -> str:
        return f"<RolePermission role={self.role_id} permission={self.permission_id}>"


class UserRole(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Assignment of a role to a user at a specific scope.

    Scope is determined by organization_id + branch_id:
      - branch_id IS NULL  = role applies org-wide
      - branch_id IS NOT NULL = role applies only within that branch

    The same role can be assigned at both org-wide and branch scope
    simultaneously — these are treated as distinct assignments because
    the unique constraint includes branch_id (NULL != a real UUID in
    the unique index, so both rows co-exist cleanly).

    organization_id is always set — every role assignment belongs to
    an org context, even when the role itself is a system role. This
    is what ties the assignment to the tenant and makes it cascadeable
    when the org is deleted.

    assigned_by tracks who granted this role (audit trail). Nullable:
    system-seeded assignments (e.g. the org owner's initial Super Admin
    role) may have no assigning user. SET NULL on delete so the audit
    trail row survives even if the assigner later leaves.
    """

    __tablename__ = "user_roles"

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
    # Always set — every assignment belongs to an org context
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL = org-wide scope; SET = branch-scoped assignment
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    branch_scope_key: Mapped[str] = mapped_column(
        String(36),
        Computed("COALESCE(branch_id, '00000000-0000-0000-0000-000000000000')"),
        nullable=False,
    )


    # Audit trail — who assigned this role
    assigned_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="user_roles",
        foreign_keys=[user_id],
    )
    role: Mapped["Role"] = relationship("Role", back_populates="user_roles")
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="user_roles",
        foreign_keys=[organization_id],
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="user_roles",
        foreign_keys=[branch_id],
    )
    assigner: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[assigned_by],
        back_populates= "assigned_roles",
        overlaps="assigned_roles",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "role_id", "organization_id", "branch_scope_key",
            name="uq_user_role_org_branch",
        ),
        Index("ix_user_roles_user_org_branch", "user_id", "organization_id", "branch_id"),
    )

    def __repr__(self) -> str:
        scope = f"branch={self.branch_id}" if self.branch_id else "org-wide"
        return f"<UserRole user={self.user_id} role={self.role_id} {scope}>"