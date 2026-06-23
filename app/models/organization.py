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

Account creation (circular FK):
- A user can only ever be created via accepted invite, OR via the
  self-serve signup flow that creates an org + its owner together,
  atomically, in one transaction. There is never a committed User row
  without an organization_id, and never a committed Organization row
  without an owner_id.
  
- RESOLVED VIA POST_UPDATE: Because `post_update=True` is configured on 
  Organization.owner, you do not need to manually manage string IDs and sequential 
  flush() commands in the service layer. You can cleanly link the Python 
  objects together in memory, and SQLAlchemy will automatically handle the 
  "INSERT Org (NULL owner) -> INSERT User -> UPDATE Org (set owner)" sequence.


Deactivation vs deletion:
- is_active / deactivated_at represent suspension — the day-to-day
  path available to the org owner (e.g. self-initiated deactivation,
  non-payment). Deactivating an org sets is_active=False and stamps
  deactivated_at; nothing is deleted, no cascades fire, no FK
  constraints are touched. The app layer is responsible for blocking
  access for an inactive org's users (a business rule, not a schema
  rule) and for allowing reactivation.
- Hard deletion (db_session.delete(org)) is restricted to the app
  owner / platform admin only — never exposed to org_owner. It is a
  deliberate, rare, audited action. When it runs, every user that
  belongs to the organization (including the owner) is deleted along
  with it: User.organization_id is ondelete="CASCADE", and owner_id
  is also ondelete="CASCADE" so the FK graph cannot deadlock against
  itself mid-cascade (see User.organization_id docstring for the full
  explanation of why RESTRICT on owner_id was rejected). The app
  layer is solely responsible for ensuring no UI/API path ever lets
  anyone delete a single owner User row independent of deleting their
  whole organization — the DB does not enforce "owner cannot be
  deleted" as an isolated guarantee, only as a side effect of org
  deletion always taking the owner with it.

  Direct owner deletion (db_session.delete(owner) while org still
  exists): because owner_id is ondelete="CASCADE", the database will
  delete the owner User row and then cascade-delete the Organization
  row, which in turn cascade-deletes all remaining member User rows
  via User.organization_id ondelete="CASCADE". This means deleting
  the owner directly effectively deletes the entire org and all its
  members — an unrecoverable side effect. The service layer MUST
  prevent any code path that would allow this; it is not blocked at
  the DB level.
"""

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    # Identity
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

    # Account status — suspension (e.g. non-payment) or owner-initiated
    # deactivation, distinct from deletion. See module docstring.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ownership
    # User who registered this org — holds Super Admin.
    # ondelete="CASCADE": when an org is deleted by the app owner, the
    # owner's User row is deleted as part of the same cascade chain
    # that deletes all members via User.organization_id. RESTRICT was
    # considered (to hard-block "delete this user" when they're an
    # owner) but it deadlocks against User.organization_id's CASCADE
    # during org deletion — the DB would see two FK actions fighting
    # over the same row in the same transaction. "Owner cannot be
    # deleted independently of their org" is therefore an app-layer
    # guarantee only: no UI/API path exposes deleting a single owner
    # User row while their organization still exists.
    #
    # WARNING: deleting the owner User row directly (outside of org
    # deletion) will cascade-delete the entire org and all its members.
    # See module docstring for the full explanation. The service layer
    # must never expose this path.
    owner_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE", use_alter=True, name="fk_organizations_owner_id"),
        nullable=True,
        index=True,
    )

    # URL identifier
    # Generated at app layer from business_name, e.g. "acme-corp"
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # Relationships
    #
    # passive_deletes=True: owner_id and User.organization_id form a
    # cycle (Organization -> owner User via owner_id; User -> Organization
    # via organization_id, with Organization.members cascading back to
    # User). If SQLAlchemy tries to compute an ORM-level delete order for
    # both edges at once, it raises CircularDependencyError — there's no
    # consistent "delete this first" answer when the same User row is
    # both the org's owner and one of its cascade-deleted members.
    # passive_deletes=True removes this edge from the ORM's own
    # dependency graph and instead trusts the database's
    # ondelete="CASCADE" (on both owner_id and organization_id) to
    # handle the actual row deletion correctly when the parent is
    # deleted. This requires the real database to enforce FKs (Postgres
    # does natively; the test suite's SQLite enables PRAGMA
    # foreign_keys=ON in conftest.py for the same reason).
    # Identity & other columns remain exactly the same...

    
    owner: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[owner_id],
        back_populates="owned_organizations",
        passive_deletes=True,
        post_update=True,  #  Breaks the ORM topological graph cycle and automates circular insertion
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
        cascade="all, delete-orphan",
    )

    challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    activity_feed: Mapped[list["UserActivityFeed"]] = relationship(
        "UserActivityFeed",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    custom_roles: Mapped[list["Role"]] = relationship(
        "Role",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    user_roles: Mapped[list["UserRole"]] = relationship(
        "UserRole",
        back_populates="organization",
        foreign_keys="UserRole.organization_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ── Wellbeing ──────────────────────────────────────────────────────────────
    wellbeing_challenges: Mapped[list["OrganizationWellbeingChallenge"]] = relationship(
        "OrganizationWellbeingChallenge",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    wellbeing_scores: Mapped[list["WellbeingScore"]] = relationship(
        "WellbeingScore",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    wellbeing_baselines: Mapped[list["UserWellbeingBaseline"]] = relationship(
        "UserWellbeingBaseline",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    health_assessments: Mapped[list["UserHealthAssessment"]] = relationship(
        "UserHealthAssessment",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="organization",
        cascade="all, delete-orphan"  # Automatically wipes invites if the organization is deleted
    )

    posts: Mapped[list["Post"]] = relationship(
        "Post",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    stories: Mapped[list["Story"]] = relationship(
        "Story",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    hashtags: Mapped[list["Hashtag"]] = relationship(
        "Hashtag",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    hashtag_trend_snapshots: Mapped[list["HashtagTrendSnapshot"]] = relationship(
        "HashtagTrendSnapshot",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    direct_messages: Mapped[list["DirectMessage"]] = relationship(
        "DirectMessage",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    group_chat_messages: Mapped[list["GroupChatMessage"]] = relationship(
        "GroupChatMessage",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    # CHANGED: User.organization_id is now NOT NULL with ondelete="CASCADE"
    # (every user belongs to exactly one org for their entire lifetime —
    # see User model docstring). Org deletion must therefore delete all
    # member User rows, not orphan them.
    #
    # cascade="all, delete-orphan" + passive_deletes=True together mean:
    # SQLAlchemy still treats members as owned children for ORM purposes
    # (e.g. removing a User from org.members marks it for deletion), but
    # passive_deletes=True stops the ORM from trying to compute its own
    # DELETE ordering for these rows when the *parent* Organization
    # itself is deleted — that job is handed entirely to the database's
    # ondelete="CASCADE" on User.organization_id. This is the other half
    # of the fix for the owner_id/members circular dependency — see the
    # comment on the `owner` relationship above for the full explanation.
    members: Mapped[list["User"]] = relationship(
        "User",
        foreign_keys="User.organization_id",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ── Denormalized org-scoped children of User ────────────────────────
    # These three carry their own organization_id (set at creation time
    # from the parent User) specifically so they can be queried or
    # RLS-scoped without a JOIN back to users — see organization_id
    # docstrings on UserSettings / UserDevice / UserAuthProvider for the
    # full rationale. Named all_user_* rather than settings/devices to
    # avoid colliding with a future per-org "settings" concept of
    # Organization's own; these are explicitly the per-user rows scoped
    # to this org, not org-level settings.
    #
    # passive_deletes=True for the same reason as `members` above: each
    # of these rows is *also* reachable via User's own cascade
    # (UserSettings/UserDevice/UserAuthProvider all cascade from
    # User.id too), so without passive_deletes the ORM would try to
    # compute two different deletion orderings for the same rows when an
    # org-wide delete cascades through both Organization and its member
    # Users simultaneously. Deferring to the DB's ondelete="CASCADE"
    # avoids that ambiguity entirely.
    all_user_settings: Mapped[list["UserSettings"]] = relationship(
        "UserSettings",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    all_user_devices: Mapped[list["UserDevice"]] = relationship(
        "UserDevice",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    all_user_auth_providers: Mapped[list["UserAuthProvider"]] = relationship(
        "UserAuthProvider",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug} owner_id={self.owner_id} active={self.is_active}>"


"""
Organization Invite model.

Design principles:
- An invite strictly belongs to an organization.
- branch_id is optional; if set, accepting the invite auto-assigns the user to that branch.
- invite_code must be unique as it forms the URL.
- invited_by links back to the user (manager) who generated the invite.
- accepted_at and expires_at manage the lifecycle of the invite.
- A User row is only ever created at the moment an invite is accepted
  (or via the org-owner self-serve signup flow — see Organization
  docstring). There is no "pending user" row prior to acceptance; the
  invite itself is the only record of intent until then.
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
        ForeignKey("users.id", ondelete="SET NULL"),  # If a user deletes their account, the invite row survives
        nullable=True,
        index=True,
    )

    # Relationships

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
        foreign_keys=[invited_by],
    )

    accepted_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="joined_via_invite",
        foreign_keys=[accepted_by_id],
    )

    # Indexes
    __table_args__ = (
        Index("ix_organization_invites_org_status", "organization_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<OrganizationInvite id={self.id} code={self.invite_code} status={self.status}>"