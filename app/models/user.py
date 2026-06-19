"""
User model — core identity only.

Design principles:
- Settings, devices, and auth providers live in their own tables
  (1-to-1 or 1-to-many)
- role_name is a plain string label — permission logic lives in a
  separate permissions table (built later).
- hashed_password is nullable: OAuth-only users have no password.
  App layer enforces that at least one auth provider exists.
- deleted_by tracks which user triggered the soft-delete (audit trail).
"""
from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from datetime import datetime, timezone
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship
from typing import Optional




class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    #Identity
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #  Role (dynamic label: permissions resolved separately)
    role_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Status
    # pending | active | inactive | suspended
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)

    # Auth (nullable: OAuth users may have no password)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    
    organization_id: Mapped[str | None] = mapped_column(
    String(36),
    ForeignKey("organizations.id", ondelete="SET NULL"),
    nullable=True,
    )
    branch_id: Mapped[str | None] = mapped_column(
    String(36),
    ForeignKey("branches.id", ondelete="SET NULL", use_alter=True, name="fk_users_branch_id"),
    nullable=True,
    index=True,
    )

    
    # Soft delete audit
    deleted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

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
    "Post",
    back_populates="user",
    foreign_keys="Post.user_id",
    )

    post_likes: Mapped[list["PostLike"]] = relationship(
        "PostLike",
        back_populates="user",
        foreign_keys="PostLike.user_id",
    )

    post_comments: Mapped[list["PostComment"]] = relationship(
        "PostComment",
        back_populates="user",
        foreign_keys="PostComment.user_id",
    )

    post_shares: Mapped[list["PostShare"]] = relationship(
        "PostShare",
        back_populates="user",
        foreign_keys="PostShare.user_id",
    )

    post_saves: Mapped[list["PostSave"]] = relationship(
        "PostSave",
        back_populates="user",
        foreign_keys="PostSave.user_id",
    )

    group_chat_messages: Mapped[list["GroupChatMessage"]] = relationship(
    "GroupChatMessage", back_populates="user", foreign_keys="GroupChatMessage.user_id",
    )
    pinned_group_chat_messages: Mapped[list["GroupChatMessage"]] = relationship(
        "GroupChatMessage", back_populates="pinned_by_user", foreign_keys="GroupChatMessage.pinned_by",
    )
    sent_direct_messages: Mapped[list["DirectMessage"]] = relationship(
        "DirectMessage", back_populates="sender", foreign_keys="DirectMessage.sender_id",
    )
    received_direct_messages: Mapped[list["DirectMessage"]] = relationship(
        "DirectMessage", back_populates="recipient", foreign_keys="DirectMessage.recipient_id",
    )

    __table_args__ = (
        Index("ix_users_org_role", "organization_id", "role_name"),
    )
    
    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role_name}>"


"""
UserSettings — all per-user toggles and preferences.

One row per user (1-to-1). Kept separate from users so the core
users table stays narrow. Settings are read less frequently than
identity fields, separating them avoids loading unused columns
on every auth check.

2FA notes:
- two_fa_enabled: user has switched 2FA on
- two_fa_method: totp | sms | email (which provider/method they use)
- two_fa_secret: encrypted TOTP secret (null if method is sms/email)
  Store this encrypted at rest — never log it.
"""



class UserSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
         String(36),
         ForeignKey("users.id", ondelete="CASCADE"),
         nullable=False,
         unique=True,
        index=True,
    )
    # Notification toggles
    email_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    challenge_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Privacy toggles
    # True = anyone can see profile; False = only the user
    public_profile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True = wellness progress visible to team/org; False = private
    show_wellness_to_team: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Theme 
    # light | dark
    theme: Mapped[str] = mapped_column(String(10), nullable=False, default="light")

    # Two-factor authentication 
    two_fa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # totp | sms | email : null until 2FA is configured
    two_fa_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Encrypted TOTP secret : null for sms/email methods
    two_fa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #user relationship
    user: Mapped["User"] = relationship("User", back_populates="settings")


    def __repr__(self) -> str:
        return f"<UserSettings user_id={self.user_id} theme={self.theme} 2fa={self.two_fa_enabled}>"


"""
UserDevice — one row per active login session / device.

Lets users see "where you're logged in" and revoke specific sessions.

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
    # Human-readable device label e.g. "Chrome on macOS", "iPhone 15"
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Raw user agent for display
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Hashed fingerprint for dedup — never store raw PII
    device_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Last known IP (store hashed or truncated for privacy if needed)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6 max = 45 chars

    # Approximate location from IP (city-level only, never precise)
    location_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    
    #user relationship
    user: Mapped["User"] = relationship("User", back_populates="devices")

    __table_args__ = (
        # Fast fetch: all active devices for a user
        Index("ix_user_devices_active", "user_id", "revoked"),
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

A single user can have multiple rows (e.g. password + google linked).
This is the correct pattern for multi-provider auth — avoids nullable
oauth_* columns polluting the users table.

provider_user_id:
  - For OAuth: the ID returned by the provider (e.g. Google sub claim)
  - For password: null (identity is the user's email)

access_token / refresh_token:
  - Store encrypted at rest.
  - Null for password provider.
  - Used to call provider APIs on behalf of user if needed.
"""


class UserAuthProvider(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_auth_providers"
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # password | google | apple | facebook | ...
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # Provider's own user identifier (null for password)
    provider_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Encrypted OAuth tokens (null for password provider)
    access_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    #user relationship
    user: Mapped["User"] = relationship("User", back_populates="auth_providers")

    __table_args__ = (
        # One row per user per provider — no duplicates
        Index("uq_user_provider", "user_id", "provider", unique=True),
        # Fast lookup: find user by provider + provider_user_id (OAuth login flow)
        Index("ix_provider_lookup", "provider", "provider_user_id"),
    )

    def __repr__(self) -> str:
        return f"<UserAuthProvider user_id={self.user_id} provider={self.provider}>"