"""
Organization model — top-level business entity.

Design principles:
- One row per registered business/organization.
- owner_id references the user who registered the org (role: overall_manager).
  This is a real FK — users table already exists.
- slug is a unique URL-safe identifier (e.g. platform.com/acme-corp).
  Generate it at the app layer from business_name; enforce uniqueness here.
- employee_count is a self-reported integer from onboarding — not derived
  from actual user rows. Keep it as a snapshot field.
- organization_type and work_model are plain strings (no enum constraint) —
  values are validated at the app/schema layer for flexibility.
- Branches and user membership (organization_id, branch_id on users) are
  wired up once those tables exist.
"""

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    # ── Identity ──────────────────────────────────────────────────────────
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_website: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Onboarding snapshot ───────────────────────────────────────────────
    # Self-reported headcount from registration — not derived from user rows
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Free strings — validated at app/schema layer (no DB enum constraint)
    # e.g. organization_type: startup | enterprise | ngo | government | ...
    organization_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # e.g. work_model: remote | hybrid | onsite
    work_model: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Ownership ─────────────────────────────────────────────────────────
    # User who registered this org — holds overall_manager role
    owner_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT", use_alter=True, name="fk_organizations_owner_id"),
        nullable=False,
        index=True,
    )

    # ── URL identifier ────────────────────────────────────────────────────
    # Generated at app layer from business_name, e.g. "acme-corp"
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # ── Relationships ──────────────────────────────────────────────────────
    owner: Mapped["User"] = relationship(
        "User",
        foreign_keys=[owner_id],
        back_populates="owned_organizations",
    )

    branches: Mapped[list["Branch"]] = relationship(
    "Branch",
    back_populates="organization",
    cascade="all, delete-orphan",
    )

    departments: Mapped[list["Department"]] = relationship(
    "Department",
    back_populates="organization",
    cascade="all, delete-orphan",
    )

    clubs: Mapped[list["Club"]] = relationship(
    "Club",
    back_populates="organization",
    cascade="all, delete-orphan",
    )

    events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="organization",
    )
    """ ── Member relationship (added when org FK on users is uncommented) ─
    members = relationship("User", foreign_keys="User.organization_id", back_populates="organization")
    """
    __table_args__ = (
        # ix_organizations_owner_id removed — already covered by index=True on the column
        Index("ix_organizations_org_type", "organization_type"),
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug} owner_id={self.owner_id}>"