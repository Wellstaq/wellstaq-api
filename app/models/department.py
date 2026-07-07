"""
Department model — a group within a branch.
 
design principles:
- A department always belongs to an organization AND a branch. There
  is no more "org-level, not yet branch-assigned" state — branch_id
  is now required at creation and cannot be null or changed to null
  later. Every department is created under a specific branch from the
  start.
- branch_id ondelete="CASCADE" (was SET NULL): deleting a branch now
  deletes its departments. This is destructive by design — see
  delete_branch.py, which must block branch deletion (or require an
  explicit department transfer/deletion step first) rather than
  silently wiping departments as a side effect.
- category_id references a Category table (not yet created) — kept as
  plain String(36) until that table exists.
- created_by is a plain audit string (no FK) — same pattern as
  elsewhere. Preserved even if creator is deleted.
- member_count is a denormalized counter — app-layer managed, never
  derived via COUNT().
- rank is an integer for ordering departments within a branch.
- avatar_initial is a short string (e.g. "HR", "EN") for display.
 

DepartmentMember design:
- A user can only belong to ONE department at a time.
  UniqueConstraint("user_id") enforces this at the DB level.
- role (plain string, e.g. "lead"/"member")  — it's a lightweight, non-authoritative label
  distinct from the new UserRoleAssignment table, which is what
  actually governs oversight/permission-relevant positions like
  team_leader. Revisit whether this field should be removed once
  UserRoleAssignment is fully wired through the department-membership
  flows — flagging rather than removing it outright since that wasn't
  explicitly confirmed.

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
 
    head_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
 
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
 
    # CHANGED: nullable=True, ondelete="SET NULL"  ->
    #          nullable=False, ondelete="CASCADE"
    # Every department now requires a branch at creation; deleting the
    # branch deletes the department (see delete_branch.py — must guard
    # against silent data loss here).
    branch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
 
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
 
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
    head: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[head_user_id],
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
        passive_deletes= True,
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
    # NEW — all UserRoleAssignment rows scoped to this department (e.g.
    # everyone holding team_leader here). See permission.py
    # UserRoleAssignment docstring.
    role_assignments: Mapped[list["UserRoleAssignment"]] = relationship(
        "UserRoleAssignment",
        back_populates="department",
        foreign_keys="UserRoleAssignment.department_id",
        cascade="all, delete-orphan",
    )
 
    def __repr__(self) -> str:
        return f"<Department id={self.id} name={self.name} branch={self.branch_id}>"
 
 
class DepartmentMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A user's membership in a department. Unchanged from before — see
    module docstring for the note on the future of the `role` field.
    """
 
    __tablename__ = "department_members"
 
    department_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )
 
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
        UniqueConstraint("user_id", name="uq_department_members_one_dept_per_user"),
        UniqueConstraint(
            "department_id", "user_id",
            name="uq_department_member_department_user",
        ),
    )
 
    def __repr__(self) -> str:
        return f"<DepartmentMember department={self.department_id} user={self.user_id}>"
 
 
class DepartmentRankSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Unchanged."""
 
    __tablename__ = "department_rank_snapshots"
 
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