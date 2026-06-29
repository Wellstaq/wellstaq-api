"""
Department model — a group within an organization.

Design principles:
- A department always belongs to an organization.
- Departments are org-level resources — created at org level, not branch-scoped.
  branch_id on the Department row is nullable and represents post-creation
  assignment to a branch, not creation scope.
- category_id references a Category table (not yet created) — kept as
  plain String(36) until that table exists.
- created_by is a plain audit string (no FK) — same pattern as deleted_by
  on User and created_by on Branch. Preserved even if creator is deleted.
- member_count is a denormalized counter — incremented/decremented by the
  app layer (or background job) on join/leave. Never derived via COUNT().
- rank is an integer for ordering departments within an org/branch.
- avatar_initial is a short string (e.g. "HR", "EN") for display purposes.

DepartmentMember design:
- A user can only belong to ONE department at a time.
  UniqueConstraint("user_id") enforces this at the DB level — attempting
  to insert a second DepartmentMember row for the same user raises
  IntegrityError.
- Reassigning a user to a different department:
      DELETE the existing DepartmentMember row
      INSERT a new one for the target department
  Both in the same transaction — user is never left without a department
  mid-request.
- role is a plain string (e.g. "lead", "member") scoped to this
  department membership, distinct from User.role_id.
- Leaving a department deletes the row — no soft delete.

DepartmentRankSnapshot design:
- Periodic snapshot of a department's rank/activity stats, computed by a
  background job (not live-queried) — same denormalized-for-speed pattern
  as member_count above.
- (department_id, period_start) indexed for fetching a department's
  history in order.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
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

    # Org scope — departments are always org-level
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable — null means org-level, not yet assigned to a branch.
    # Set post-creation via a separate assignment endpoint.
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Audit — plain string, no FK. Preserved even if creator is deleted.
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Denormalized counter — managed by app layer / background job.
    # Never use COUNT() in queries.
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
    """
    A user's membership in a department.

    One user → one department at a time.
    UniqueConstraint("user_id") is the DB-level enforcement.

    To reassign a user to a different department:
        await db.delete(existing_membership)
        await db.flush()
        db.add(DepartmentMember(department_id=new_dept_id, user_id=user_id))
        await db.flush()

    Both operations must happen in the same transaction so the user is
    never left without a department mid-request.
    """

    __tablename__ = "department_members"

    # No separate index=True on department_id — covered by the composite
    # unique constraint below, which already indexes it as the leading column.
    department_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Indexed for "which department does this user belong to" lookups.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Department-scoped role — distinct from User.role_id (the org/platform role).
    # e.g. "lead", "member". Nullable — not every membership has a sub-role.
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)

    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    # ── Relationships ──────────────────────────────────────────────────────
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
        # One user → one department at a time.
        # This is the primary business rule constraint.
        UniqueConstraint("user_id", name="uq_department_members_one_dept_per_user"),
        # Redundant given the above, but kept for a clearer error message
        # on the specific "re-joining the same department" case.
        UniqueConstraint(
            "department_id", "user_id",
            name="uq_department_member_department_user",
        ),
    )

    def __repr__(self) -> str:
        return f"<DepartmentMember department={self.department_id} user={self.user_id}>"


class DepartmentRankSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Periodic snapshot of a department's rank/activity stats, computed by
    a background job — same denormalized-for-speed pattern as
    Department.member_count. Never derived live via aggregate queries.
    """

    __tablename__ = "department_rank_snapshots"

    # No separate index=True — covered by the composite index below.
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

    # ── Relationships ──────────────────────────────────────────────────────
    department: Mapped["Department"] = relationship(
        "Department",
        back_populates="rank_snapshots",
        foreign_keys=[department_id],
    )

    __table_args__ = (
        Index(
            "ix_department_rank_snapshots_dept_period_start",
            "department_id", "period_start",
        ),
    )

    def __repr__(self) -> str:
        return f"<DepartmentRankSnapshot department={self.department_id} rank={self.rank}>"