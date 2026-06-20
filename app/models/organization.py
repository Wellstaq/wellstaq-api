"""
Organization model: top-level business entity.

Design principles:
- One row per registered business/organization.
- owner_id references the user who registered the org (role: Super Admin).
  This is a real FK.
- slug is a unique URL-safe identifier (e.g. platform.com/acme-corp).
- employee_count is a self-reported integer from onboarding — not derived
  from actual user rows.
- organization_type and work_model are plain strings (no enum constraint)
  values are validated at the app/schema layer for flexibility.
"""

from sqlalchemy import ForeignKey, Index, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    #Identity
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_website: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Onboarding snapshot
    # Self-reported headcount from registration. not derived from user rows
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Free strings — validated at app/schema layer (no DB enum constraint)
    # e.g. organization_type: startup | enterprise | ngo | government | etc
    organization_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # e.g. work_model: remote | hybrid | onsite
    work_model: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Ownership
    # User who registered this org  holds Super Admin
    owner_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT", use_alter=True, name="fk_organizations_owner_id"),
        nullable=False,
        index=True,
    )

    # URL identifier
    # Generated at app layer from business_name, e.g. "acme-corp"
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # Relationships
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

    challenges: Mapped[list["Challenge"]] = relationship(
    "Challenge",
    back_populates="organization",
    )
   
    invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="organization",
        cascade="all, delete-orphan" # Automatically wipes invites if the organization is deleted
    )
    posts: Mapped[list["Post"]] = relationship(
    "Post",
    back_populates="organization",
    )

    hashtag_trend_snapshots: Mapped[list["HashtagTrendSnapshot"]] = relationship(
        "HashtagTrendSnapshot", back_populates="organization",
    )
    direct_messages: Mapped[list["DirectMessage"]] = relationship("DirectMessage", back_populates="organization")
    notifications: Mapped[list["Notification"]] = relationship("Notification", back_populates="organization")
    
    """ ── Member relationship (added when org FK on users is uncommented) ─
    members = relationship("User", foreign_keys="User.organization_id", back_populates="organization")
    """
    

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug} owner_id={self.owner_id}>"

"""
Organization Invite model.

Design principles:
- An invite strictly belongs to an organization.
- branch_id is optional; if set, accepting the invite auto-assigns the user to that branch.
- invite_code must be unique as it forms the URL.
- invited_by links back to the user (manager) who generated the invite.
- accepted_at and expires_at manage the lifecycle of the invite.
"""

class OrganizationInvite(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization_invites"

    # Foreign Keys
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
    )

    invited_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Invite Data 
    invite_code: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    invited_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # status: pending | accepted | expired | revoked
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # Timestamps
    # created_at is handled by TimestampMixin automatically
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    
    accepted_by_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"), # If a user deletes their account, the invite row survives
        nullable=True,
        index=True,
    )

    #  Relationships


    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="invites", 
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="invites",  
        foreign_keys=[branch_id],
    )

    inviter: Mapped["User"] = relationship(
        "User",
        back_populates="sent_invites",
        foreign_keys=[invited_by],  # Raw column reference works perfectly here
    )

    accepted_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="joined_via_invite",
        foreign_keys=[accepted_by_id], # Raw column reference works perfectly here
    )

    # Indexes
    __table_args__ = (
        Index("ix_organization_invites_org_status", "organization_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<OrganizationInvite id={self.id} code={self.invite_code} status={self.status}>"