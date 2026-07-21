"""
User model - core identity only.

Design principles:
- Settings, devices, and auth providers live in their own tables
  (1-to-1 or 1-to-many). Each of those tables also carries its own
  organization_id (denormalized from this table at creation time) so
  that tenant isolation can be enforced directly on the row — via
  Postgres Row-Level Security or equivalent — without requiring a JOIN
  back to users on every query. This is a deliberate redundancy: the
  join-based path and the column-based path carry the same logical
  risk if access control is bypassed entirely, but only the
  denormalized column lets the database itself refuse cross-tenant
  rows at the index/policy level. See organization_id docstring below
  for why it is NOT NULL here.

  IMPORTANT — denormalized organization_id consistency: child rows
  (UserSettings, UserDevice, UserAuthProvider) carry their own
  organization_id FK which must always equal the parent User's
  organization_id. The DB cannot enforce this without a composite FK
  referencing a UNIQUE(id, organization_id) index on users — which is
  valid Postgres but unsupported by SQLite (used in tests). This
  consistency is therefore an app-layer responsibility: always set
  child.organization_id = user.organization_id on insert, never
  update it independently. A future Postgres-only migration can add
  the composite FK as a hard enforcement layer. Until then, any bug
  that sets child.organization_id != user.organization_id would
  corrupt RLS tenant isolation silently — treat this as a critical
  invariant in all service-layer code.

branch_id:
- Every user now MUST belong to exactly one branch — their "home"
  branch. This is completely separate from oversight: a user's role
  (User.role_id) may be exercised across MULTIPLE branches or
  departments via UserRoleAssignment rows (see permission.py), but
  User.branch_id always answers exactly one question: "where does
  this person live/belong as a member." It is never a list, never
  ambiguous, and is NOT updated by role-assignment operations.
- Every organization has an auto-created Headquarters branch (see
  signup_service). "Org-wide" positions like super_admin are simply
  users whose home branch is Headquarters — there is no separate
  "no branch" state anymore.
- ondelete: Branch deletion must now either
  reassign every user whose home branch is the one being deleted, or
  be blocked outright while such users exist — see delete_branch.py.
  A silent SET NULL would leave a NOT NULL column null, which the DB
  will reject anyway; this FK's ondelete behavior needs explicit
  service-layer handling before a branch can ever be deleted (this is
  the "creepy" edge case flagged during design — home branch vs.
  oversight assignments are different relationships to the same table
  and must be treated as such).
"""


from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.constants import USER_STATUSES





class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"
    __mapper_args__ = {"confirm_deleted_rows": False}

    # Identity
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    role_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True
    )

    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Every user has exactly one home branch now — see module docstring
    # above. ondelete="CASCADE": if a branch is ever deleted while a user
    # still calls it home, that user is deleted too. This is a safety
    # fallback only — the real guard is in delete_branch.py, which
    # transfers every home-branch member OUT before it ever issues the
    # actual DELETE, so this CASCADE should never fire against a real,
    # still-wanted user in that path. It exists so a full Organization
    # deletion (which cascades to Branch, which would otherwise leave
    # dangling User rows) can complete in one atomic operation.
    branch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="CASCADE", use_alter=True, name="fk_users_branch_id"),
        nullable=False,
        index=True,
    )

    # Location
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    settings: Mapped["UserSettings"] = relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    devices: Mapped[list["UserDevice"]] = relationship(
        "UserDevice", back_populates="user", cascade="all, delete-orphan"
    )
    auth_providers: Mapped[list["UserAuthProvider"]] = relationship(
        "UserAuthProvider", back_populates="user", cascade="all, delete-orphan"
    )
    owned_organizations: Mapped[list["Organization"]] = relationship(
        "Organization",
        back_populates="owner",
        foreign_keys="Organization.owner_id",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="members",
        foreign_keys=[organization_id],
    )
    managed_branches: Mapped[list["Branch"]] = relationship(
        "Branch",
        back_populates="manager",
        foreign_keys="Branch.manager_id",
    )
    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="members",
        foreign_keys=[branch_id],
    )
    club_memberships: Mapped[list["ClubMember"]] = relationship(
        "ClubMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    department_memberships: Mapped[list["DepartmentMember"]] = relationship(
        "DepartmentMember",
        back_populates="user",
        foreign_keys="DepartmentMember.user_id",
    )
    created_events: Mapped[list["Event"]] = relationship(
        "Event",
        back_populates="creator",
        foreign_keys="Event.created_by",
    )
    event_participations: Mapped[list["EventParticipant"]] = relationship(
        "EventParticipant",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    created_challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge",
        back_populates="creator",
        foreign_keys="Challenge.created_by",
    )
    challenge_participations: Mapped[list["ChallengeParticipant"]] = relationship(
        "ChallengeParticipant",
        back_populates="user",
        foreign_keys="ChallengeParticipant.user_id",
    )

    challenge_progress_entries: Mapped[list["ChallengeProgressEntry"]] = relationship(
        "ChallengeProgressEntry",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ChallengeProgressEntry.user_id",
    )
    sent_invites: Mapped[list["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="inviter",
        foreign_keys="OrganizationInvite.invited_by",
    )
    joined_via_invite: Mapped[Optional["OrganizationInvite"]] = relationship(
        "OrganizationInvite",
        back_populates="accepted_by_user",
        foreign_keys="OrganizationInvite.accepted_by_id",
        uselist=False,
    )
    posts: Mapped[list["Post"]] = relationship(
        "Post", back_populates="user", foreign_keys="Post.user_id",
    )
    post_likes: Mapped[list["PostLike"]] = relationship(
        "PostLike", back_populates="user", foreign_keys="PostLike.user_id",
    )
    post_comments: Mapped[list["PostComment"]] = relationship(
        "PostComment", back_populates="user", foreign_keys="PostComment.user_id",
    )
    post_shares: Mapped[list["PostShare"]] = relationship(
        "PostShare", back_populates="user", foreign_keys="PostShare.user_id",
    )
    post_saves: Mapped[list["PostSave"]] = relationship(
        "PostSave", back_populates="user", foreign_keys="PostSave.user_id",
    )
    stories: Mapped[list["Story"]] = relationship(
        "Story", back_populates="user", cascade="all, delete-orphan",
    )
    group_chat_messages: Mapped[list["GroupChatMessage"]] = relationship(
        "GroupChatMessage", back_populates="user", foreign_keys="GroupChatMessage.user_id",
    )
    pinned_group_chat_messages: Mapped[list["GroupChatMessage"]] = relationship(
        "GroupChatMessage", back_populates="pinned_by_user", foreign_keys="GroupChatMessage.pinned_by",
    )

    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user", foreign_keys="Notification.user_id",
    )
    triggered_notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="actor", foreign_keys="Notification.actor_id",
    )
    wellbeing_baselines: Mapped[list["UserWellbeingBaseline"]] = relationship(
        "UserWellbeingBaseline", back_populates="user", cascade="all, delete-orphan",
    )
    priorities: Mapped[list["UserPriority"]] = relationship(
        "UserPriority", back_populates="user", cascade="all, delete-orphan",
    )
    daily_checkins: Mapped[list["DailyCheckin"]] = relationship(
        "DailyCheckin", back_populates="user", cascade="all, delete-orphan",
    )
    activity_feed: Mapped[list["UserActivityFeed"]] = relationship(
        "UserActivityFeed", back_populates="user", cascade="all, delete-orphan",
    )
    health_assessments: Mapped[list["UserHealthAssessment"]] = relationship(
        "UserHealthAssessment", back_populates="user", cascade="all, delete-orphan",
    )
    wellbeing_scores: Mapped[list["WellbeingScore"]] = relationship(
        "WellbeingScore", back_populates="user", cascade="all, delete-orphan",
    )
    activity_logs: Mapped[list["ActivityLog"]] = relationship(
        "ActivityLog", back_populates="user", cascade="all, delete-orphan"
    )
    engagement_logs: Mapped[list["EngagementLog"]] = relationship(
        "EngagementLog", back_populates="user", cascade="all, delete-orphan"
    )
    leaderboard_snapshots: Mapped[list["LeaderboardSnapshot"]] = relationship(
        "LeaderboardSnapshot",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    role: Mapped[Optional["Role"]] = relationship(
        "Role",
        back_populates="users",
        foreign_keys=[role_id],
    )
    user_permissions: Mapped[list["UserPermission"]] = relationship(
        "UserPermission",
        back_populates="user",
        foreign_keys="UserPermission.user_id",
        cascade="all, delete-orphan",
    )
    # NEW — every branch/department this user's single role is
    # actively exercised in (oversight), completely separate from
    # branch_id above (home/membership). See permission.py
    # UserRoleAssignment docstring.
    role_assignments: Mapped[list["UserRoleAssignment"]] = relationship(
        "UserRoleAssignment",
        back_populates="user",
        foreign_keys="UserRoleAssignment.user_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_users_org_role", "organization_id", "role_id"),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in USER_STATUSES)})",
            name="ck_user_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role_id}>"




"""
UserSettings — all per-user toggles and preferences.

One row per user (1-to-1). Kept separate from users so the core
users table stays narrow. Settings are read less frequently than
identity fields; separating them avoids loading unused columns on
every auth check.

organization_id is denormalized from the parent User at creation time
(set once, never changed independently — it should always equal
user.organization_id for the row's lifetime). This lets org-scoped
operations (e.g. "find all users in org X with 2FA disabled", or a
Postgres Row-Level Security policy) run directly against this table
without a JOIN back to users. ondelete="CASCADE" on organization_id
is belt-and-suspenders: in practice the row is always deleted first
via the user_id CASCADE when the user itself is removed, but the
org-level FK ensures no row can ever outlive its organization even if
reached by an unexpected path.

CONSISTENCY INVARIANT: organization_id here must always equal the
parent User's organization_id. See User module docstring for why the
DB cannot enforce this on SQLite (test DB) and what the app-layer
responsibility is.

2FA notes:
- two_fa_enabled: user has switched 2FA on
- two_fa_method: totp | sms | email. Null until 2FA is configured;
  must not be set if two_fa_enabled is False.
- two_fa_secret: encrypted TOTP secret (null if method is sms/email).
  Store this encrypted at rest — never log it. Must be set if
  two_fa_method='totp'; must be null otherwise.

2FA consistency is enforced via CHECK constraints:
  - two_fa_enabled=True requires two_fa_method to be set
  - two_fa_enabled=False requires two_fa_method AND secret to be null
  - two_fa_method='totp' requires two_fa_secret to be set
  - two_fa_method != 'totp' requires two_fa_secret to be null
"""

TWO_FA_METHODS = ("totp", "sms", "email")
THEMES = ("light", "dark")


class UserSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Denormalized from User.organization_id — see module docstring.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Notification toggles
    email_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    challenge_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Privacy toggles
    public_profile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    show_wellness_to_team: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Theme — constrained to known values via ck_user_settings_theme
    theme: Mapped[str] = mapped_column(String(10), nullable=False, default="light")

    # Two-factor authentication — consistency enforced via CHECK constraints.
    two_fa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    two_fa_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    two_fa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="settings")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="all_user_settings"
    )

    __table_args__ = (
        # two_fa_enabled and two_fa_method must be consistent.
        # SQLite stores booleans as 0/1 integers.
        CheckConstraint(
            "(two_fa_enabled = 0 AND two_fa_method IS NULL) "
            "OR (two_fa_enabled = 1 AND two_fa_method IS NOT NULL)",
            name="ck_user_settings_2fa_enabled_method_consistent",
        ),
        # two_fa_method='totp' requires a secret; sms/email must have
        # secret=NULL; no method set means secret must also be NULL.
        CheckConstraint(
            "(two_fa_method = 'totp' AND two_fa_secret IS NOT NULL) "
            "OR (two_fa_method != 'totp' AND two_fa_secret IS NULL) "
            "OR (two_fa_method IS NULL AND two_fa_secret IS NULL)",
            name="ck_user_settings_2fa_totp_secret_consistent",
        ),
        # Constrain theme to known values.
        CheckConstraint(
            f"theme IN ({', '.join(repr(t) for t in THEMES)})",
            name="ck_user_settings_theme",
        ),
        # Constrain two_fa_method to known values when set.
        CheckConstraint(
            f"two_fa_method IS NULL OR two_fa_method IN ({', '.join(repr(m) for m in TWO_FA_METHODS)})",
            name="ck_user_settings_2fa_method",
        ),
    )

    def __repr__(self) -> str:
        return f"<UserSettings user_id={self.user_id} theme={self.theme} 2fa={self.two_fa_enabled}>"


"""
UserDevice — one row per active login session / device.

Lets users see "where you're logged in" and revoke specific sessions.

organization_id is denormalized from the parent User at creation time
(see UserSettings docstring for the full rationale — same pattern
applies here, and matters even more for this table since it carries
ip_address and location_label: being able to filter/revoke "every
active device session across org X" directly, without a JOIN, is
operationally important for incident response, e.g. after a breach).

CONSISTENCY INVARIANT: organization_id here must always equal the
parent User's organization_id. See User module docstring.

device_fingerprint:
    A hash of (user_agent + ip + optional client device id).
    Used to detect the same device across sessions without storing PII raw.

last_seen_at:
    Updated on every authenticated request — use a background task or
    a debounced update (e.g. only write if > 5 min since last update)
    to avoid a DB write on every API call.

revoked:
    True = user explicitly logged out this device / session.
    Filter active sessions with: WHERE revoked = false AND user_id = ?
"""


class UserDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_devices"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalized from User.organization_id — see module docstring.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    location_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="devices")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="all_user_devices"
    )

    __table_args__ = (
        Index("ix_user_devices_active", "user_id", "revoked"),
        Index("ix_user_devices_org_active", "organization_id", "revoked"),
    )

    def __repr__(self) -> str:
        return f"<UserDevice user_id={self.user_id} device={self.device_name} revoked={self.revoked}>"


"""
UserAuthProvider — one row per authentication method per user.

Supports:
  - Local password auth  (provider = "password")
  - Google OAuth         (provider = "google")
  - Apple OAuth          (provider = "apple")
  - Any future provider  (just add a new row)

organization_id is denormalized from the parent User at creation time.
This table holds encrypted OAuth tokens — among the most sensitive rows
in the schema — so being able to scope and revoke by organization_id
directly (e.g. "revoke every linked auth provider for org X") without
a JOIN matters specifically here.

CONSISTENCY INVARIANT: organization_id here must always equal the
parent User's organization_id. See User module docstring.

Provider integrity rules (enforced via CHECK constraints):
  - provider != 'password' => provider_user_id IS NOT NULL
  - provider = 'password'  => provider_user_id IS NULL
  - provider = 'password'  => access_token IS NULL AND refresh_token IS NULL
"""


class UserAuthProvider(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_auth_providers"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalized from User.organization_id — see module docstring.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # password | google | apple | facebook | ...
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # Provider's own user identifier (null for password, required for OAuth)
    provider_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Encrypted OAuth tokens (null for password provider)
    access_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="auth_providers")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="all_user_auth_providers"
    )

    __table_args__ = (
        # One row per user per provider
        Index("uq_user_provider", "user_id", "provider", unique=True),
        # Fast lookup: find user by provider + provider_user_id (OAuth login)
        Index("ix_provider_lookup", "provider", "provider_user_id"),
        # Fast fetch: all linked providers org-wide (incident response)
        Index("ix_user_auth_providers_org", "organization_id"),
        # OAuth providers must supply a provider_user_id (sub claim);
        # password auth identity is the user's email so it must be null.
        CheckConstraint(
            "(provider = 'password' AND provider_user_id IS NULL) "
            "OR (provider != 'password' AND provider_user_id IS NOT NULL)",
            name="ck_user_auth_provider_id_consistency",
        ),
        # OAuth tokens must not be stored for password auth rows.
        CheckConstraint(
            "provider != 'password' "
            "OR (access_token IS NULL AND refresh_token IS NULL)",
            name="ck_user_auth_provider_password_no_tokens",
        ),
    )

    def __repr__(self) -> str:
        return f"<UserAuthProvider user_id={self.user_id} provider={self.provider}>"