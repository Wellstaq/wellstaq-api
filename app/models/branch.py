"""
Branch model — a subdivision of an organization.

Design principles:
- A branch belongs to exactly one organization.
- manager_id references a User (branch_manager role) — no separate manager
  table; managers are just users with a role label.
- created_by references the User who created this branch (audit trail).
- Both manager_id and created_by are RESTRICT on delete — you must
  reassign or remove the branch before deleting those users.
- manager_id is nullable: a branch can exist without an assigned manager yet.
"""

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Branch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "branches"

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
    # Nullable — a branch may be created before a manager is assigned
    manager_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    # Intentionally no FK — audit trail, same pattern as deleted_by on User
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="branches",
        foreign_keys=[organization_id],
    )

    manager: Mapped["User"] = relationship(
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
   
    def __repr__(self) -> str:
        return f"<Branch id={self.id} name={self.name} org={self.organization_id}>"