"""
Department model — a group within an organization or branch.

Design principles:
- A department always belongs to an organization.
- branch_id is nullable: null means the department sits at org level,
  not scoped to a specific branch.
- category_id references a Category table (not yet created) — kept as
  plain String(36) until that table exists.
- created_by is a plain audit string (no FK) — same pattern as deleted_by
  on User and created_by on Branch. Preserved even if creator is deleted.
- member_count is a denormalized counter — incremented/decremented by the
  app layer (or background job) on join/leave. Never derived via COUNT().
- rank is an integer for ordering departments within an org/branch.
- avatar_initial is a short string (e.g. "HR", "EN") for display purposes.
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Department(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "departments"

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_initial: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Category (FK added when categories table exists) ──────────────────
    #category_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Org / branch scope ────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable — null means org-level department, not scoped to a branch
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    # Plain string — no FK, audit trail preserved even if creator is deleted
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ── Denormalized counter ───────────────────────────────────────────────
    # Managed by app layer / background job — never use COUNT() in queries
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="departments",
        foreign_keys=[organization_id],
    )
    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="departments",
        foreign_keys=[branch_id],
    )

    """ ── Category relationship (added when categories table exists) ──────
    category = relationship("Category", back_populates="departments")
    """

    def __repr__(self) -> str:
        return f"<Department id={self.id} name={self.name} org={self.organization_id}>"