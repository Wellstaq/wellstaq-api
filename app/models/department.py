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

DepartmentMember design:
- Join model for users belonging to a department.
- Unique (department_id, user_id) — a user can only join a given
  department once. Leaving deletes the row
- role is a plain string (e.g. "lead", "member") scoped to this
  department membership, distinct from User.role_name.

DepartmentRankSnapshot design:
- Periodic snapshot of a department's rank/activity stats, computed by a
  background job (not live-queried) — same denormalized-for-speed pattern
  as member_count above.
- (department_id, period_start) indexed for fetching a department's
  history in order.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Department(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "departments"

    # Identity
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_initial: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    category_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Org / branch scope
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

    # Audit
    # Plain string — no FK, audit trail preserved even if creator is deleted
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Denormalized counter
    # Managed by app layer / background job — never use COUNT() in queries
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
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

    members: Mapped[list["DepartmentMember"]] = relationship(
        "DepartmentMember",
        back_populates="department",
        cascade="all, delete-orphan",
    )
    invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="invited_department",
        foreign_keys="OrganizationInvite.invited_department_id",
    )

    rank_snapshots: Mapped[list["DepartmentRankSnapshot"]] = relationship(
        "DepartmentRankSnapshot",
        back_populates="department",
        cascade="all, delete-orphan",
    )

    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="departments",
    )

    def __repr__(self) -> str:
        return f"<Department id={self.id} name={self.name} org={self.organization_id}>"


class DepartmentMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A user's membership in a department. Unique per (department_id,
    user_id) — a user can only join a given department once. Leaving
    deletes the row, same insert/delete pattern as post_likes."""

    __tablename__ = "department_members"

    # No separate index=True on department_id — it's the leading column of
    # the unique constraint below, which already indexes it.
    department_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Not the leading column of the unique constraint, so it keeps its own
    # index for "all departments this user belongs to" lookups.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    department: Mapped["Department"] = relationship(
        "Department",
        back_populates="members",
        foreign_keys=[department_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="department_memberships",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        UniqueConstraint("department_id", "user_id", name="uq_department_member_department_user"),
    )

    def __repr__(self) -> str:
        return f"<DepartmentMember department={self.department_id} user={self.user_id}>"


class DepartmentRankSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Periodic snapshot of a department's rank/activity stats, computed by
    a background job — same denormalized-for-speed pattern as
    Department.member_count. Never derived live via aggregate queries."""

    __tablename__ = "department_rank_snapshots"

    # No separate index=True — covered by the (department_id, period_start)
    # composite index below.
    department_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )

    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_activities: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_daily_steps: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    department: Mapped["Department"] = relationship(
        "Department",
        back_populates="rank_snapshots",
        foreign_keys=[department_id],
    )

    __table_args__ = (
        Index("ix_department_rank_snapshots_dept_period_start", "department_id", "period_start"),
    )

    def __repr__(self) -> str:
        return f"<DepartmentRankSnapshot department={self.department_id} rank={self.rank}>"