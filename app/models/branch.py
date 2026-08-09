"""
Branch model — a subdivision of an organization.

Design principles:
- A branch belongs to exactly one organization.
- manager_id references a User (branch_manager role) — no separate manager
  table; managers are just users with a role label.
- created_by is a plain audit string (no FK) — preserved even if the
  creator is later deleted, same pattern as Club and Department.
- manager_id is SET NULL on delete — a manager can be deleted without
  any precondition. The branch survives and sits without a manager until
  one is reassigned. This matches the product rule that only the
  Super Admin (org owner) cannot be deleted independently; all other
  users including branch managers can be deleted at any time.
- manager_id is nullable: a branch can exist without an assigned manager.
- organization_id is CASCADE on delete — when the org is deleted (app
  owner only action), all its branches are deleted with it.

Headquarters branch:
  Every org has exactly ONE protected branch created automatically at
  registration (see signup_service.py). It is marked with
  is_headquarters=True. The service layer uses this flag to:
    - Block deletion (delete_branch.py).
    - Block renaming (update_branch.py / edit_branch).
    - Block any regular branch from claiming the "Headquarters" name
      (create_branch.py).
  The org owner and any other "org-wide" users live here as their home
  branch. There is no separate "org-wide / no branch" state — HQ is
  the implementation of that concept.

Scope / RBAC notes:
- roles: A branch can have MANY custom Role rows (one-to-many as of the
  role-isolation redesign). When the branch is deleted, all its custom
  roles are cascade-deleted (Role.branch_id ondelete="CASCADE").
- role_assignments: Every UserRoleAssignment row scoped to this branch
  (e.g. branch-level oversight positions like branch_manager).
  Cascade-deleted when the branch is deleted — does NOT affect
  User.branch_id (home branch) for any user; that FK is handled
  independently by delete_branch.py before deletion proceeds.
- user_permissions: All UserPermission rows scoped to this branch.
  Cascade-deleted by the DB FK when the branch is removed.
- clubs: Cascade-deleted when the branch is deleted.
- departments: Cascade-deleted when the branch is deleted (branch_id
  is now NOT NULL on Department — see department.py).
"""

from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Branch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "branches"
    __mapper_args__ = {"confirm_deleted_rows": False}
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_branch_org_name"),
        CheckConstraint("length(trim(name)) > 0", name="chk_branch_name_not_empty"),
    )

    # ── Core ──────────────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # True for the single auto-created "Headquarters" branch per org.
    # Protected by the service layer against deletion and renaming.
    # Only ever set to True by signup_service — never by any admin
    # action. Defaults False for all regular branches.
    is_headquarters: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    manager_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="fk_branch_manager_id"),
        nullable=True,
        index=True,
    )

    # Plain audit string — no FK. Preserved even if the creator is deleted.
    # NULL during org creation (user doesn't exist yet); backfilled by
    # signup_service once user.id is available.
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="branches",
        foreign_keys=[organization_id],
    )
    manager: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="managed_branches",
        foreign_keys=[manager_id],
    )
    # Home-branch members. Every User row requires branch_id (NOT NULL),
    # so this is a true membership list, never "users with no branch."
    # passive_deletes=True prevents SQLAlchemy from issuing SET NULL
    # before the delete — the RESTRICT FK on User.branch_id means the
    # service layer must transfer members before any branch can be
    # deleted (see delete_branch.py).
    members: Mapped[list["User"]] = relationship(
        "User",
        back_populates="branch",
        foreign_keys="User.branch_id",
        passive_deletes=True,
    )
    departments: Mapped[list["Department"]] = relationship(
        "Department",
        back_populates="branch",
        cascade="all, delete-orphan",
    )
    clubs: Mapped[list["Club"]] = relationship(
        "Club",
        back_populates="branch",
        foreign_keys="Club.branch_id",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="branch",
        cascade="all, delete-orphan",
    )
    challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge",
        back_populates="branch",
    )
    invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="branch",
        cascade="all, delete-orphan",
    )
    posts: Mapped[list["Post"]] = relationship(
        "Post",
        back_populates="branch",
    )
    stories: Mapped[list["Story"]] = relationship(
        "Story",
        back_populates="branch",
        passive_deletes=True,  # let the DB SET NULL handle it, not the ORM
    )
    # ── RBAC ──────────────────────────────────────────────────────────────────
    # One-to-many: a branch can have many custom roles, all isolated
    # to this branch. Cascade-deleted with the branch.
    roles: Mapped[list["Role"]] = relationship(
        "Role",
        back_populates="branch",
        foreign_keys="Role.branch_id",
        cascade="all, delete-orphan",
    )
    # Oversight assignments scoped to this branch (e.g. who is branch_manager
    # here). Separate from members above — a user can oversee a branch without
    # their home branch being this one. Cascade-deleted with the branch.
    role_assignments: Mapped[list["UserRoleAssignment"]] = relationship(
        "UserRoleAssignment",
        back_populates="branch",
        foreign_keys="UserRoleAssignment.branch_id",
        cascade="all, delete-orphan",
    )
    # Branch-scoped permission grants. No ORM cascade — DB FK ondelete="CASCADE"
    # handles it. Adding ORM cascade too would cause a double-delete race.
    user_permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="branch",
        foreign_keys="UserPermission.branch_id",
    )
    activity_logs: Mapped[list["ActivityLog"]] = relationship(
        "ActivityLog", back_populates="branch", cascade="all, delete-orphan"
    )
    engagement_logs: Mapped[list["EngagementLog"]] = relationship(
        "EngagementLog", back_populates="branch", cascade="all, delete-orphan"
    )
    leaderboard_snapshots = relationship(
        "LeaderboardSnapshot",
        back_populates="branch",
        cascade="all, delete-orphan"
    )
    
    targets = relationship(
        "Target",
        back_populates="branch",
        cascade="all, delete-orphan"
    )
    
    kpi_snapshots = relationship(
        "KPISnapshot",
        back_populates="branch",
        cascade="all, delete-orphan"
    )


    def __repr__(self) -> str:
        hq = " [HQ]" if self.is_headquarters else ""
        return f"<Branch id={self.id} name={self.name} org={self.organization_id}{hq}>"