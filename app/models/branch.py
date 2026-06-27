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

Scope / RBAC notes:
- role: one-to-one with the branch-scoped Role row (if any). When the
  branch is deleted, its scoped Role is cascade-deleted.
- user_permissions: all UserPermission rows whose branch_id points here.
  When the branch is deleted, those branch-scoped grants are cascade-
  deleted automatically (FK ondelete="CASCADE" on UserPermission.branch_id).
"""

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Branch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "branches"
    __mapper_args__ = {"confirm_deleted_rows": False}
    __table_args__ = (
        # Ensures branch names are unique *within* the same organization
        UniqueConstraint("organization_id", "name", name="uq_branch_org_name"),
        # Prevents empty strings or whitespace-only names at the database layer
        CheckConstraint("length(trim(name)) > 0", name="chk_branch_name_not_empty"),
    )

    # ── Organization ──────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Manager (branch_manager role user) ────────────────────────────────
    # Nullable — a branch can exist without an assigned manager.
    # SET NULL on delete — manager can be deleted freely; the branch
    # survives and manager_id is nulled until reassigned. Only the
    # org Super Admin (Organization.owner_id) cannot be deleted
    # independently — that rule is enforced at the service layer, not here.
    manager_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="fk_branch_manager_id"),
        nullable=True,
        index=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    # Intentionally no FK — audit trail, same pattern as Club/Department.
    # Preserved even if the creator is later deleted.
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
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

    members: Mapped[list["User"]] = relationship(
        "User",
        back_populates="branch",
        foreign_keys="User.branch_id",
    )
    departments: Mapped[list["Department"]] = relationship(
        "Department",
        back_populates="branch",
    )

    events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="branch",
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

    # ── RBAC ──────────────────────────────────────────────────────────────
    # Branch-scoped Role row (one per branch at most). When the branch is
    # deleted, its scoped Role is cascade-deleted via Role.branch_id FK.
    role: Mapped[Optional["Role"]] = relationship(
        "Role",
        back_populates="branch",
        foreign_keys="Role.branch_id",
        cascade="all, delete-orphan",
    )

    # All UserPermission grants scoped to this branch. When the branch is
    # deleted, these rows are cascade-deleted via UserPermission.branch_id FK,
    # so no user retains a dangling branch-scoped permission grant.
    user_permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="branch",
        foreign_keys="UserPermission.branch_id",
        # No cascade here — the DB FK (ondelete="CASCADE") handles deletion.
        # Letting SQLAlchemy also cascade would cause a double-delete race.
    )

    def __repr__(self) -> str:
        return f"<Branch id={self.id} name={self.name} org={self.organization_id}>"