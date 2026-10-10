"""
app/core/constants.py

Single source of truth for all magic strings used across services.

Values here are grounded in:
  - app/auth/jwt.py          — token type literals ("access", "refresh")
  - app/auth/dependencies.py — ROLE_HIERARCHY keys
  - DB column check constraints (status values, invite status, etc.)

Import from here — never hardcode these values in service logic.
"""

from enum import StrEnum
import re


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"


class InviteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class OAuthProvider(StrEnum):
    PASSWORD = "password"
    GOOGLE = "google"
    APPLE = "apple"


class TwoFaMethod(StrEnum):
    TOTP = "totp"
    SMS = "sms"
    EMAIL = "email"


class TokenType(StrEnum):
    """
    Must match the 'type' claim literals set by jwt.py's _create_token().
    jwt.py sets type="access" for access tokens, type="refresh" for refresh
    tokens. Extra token types below are set manually in extra_claims dicts
    and checked by service code — they are never issued by _create_token
    directly.
    """
    ACCESS = "access"
    REFRESH = "refresh"
    EMAIL_VERIFICATION = "email_verification"
    INVITE_VERIFICATION = "invite_verification"
    TWO_FA_CHALLENGE = "2fa_challenge"
    PASSWORD_RESET =  "password_reset"
    


class RoleName(StrEnum):
    """
    System role names as stored in the DB (Role.name column).
    These are title-cased display names — NOT the same as the
    ROLE_HIERARCHY keys in dependencies.py, which are lowercase
    internal identifiers used only for hierarchy comparisons.

    """
    SUPER_ADMIN = "super_admin"   # was "Super Admin"
    ADMIN = "admin"
    BRANCH_MANAGER = "branch_manager"
    TEAM_LEADER = "team_leader"
    MEMBER = "member"


class ActivityType(StrEnum):
    ACCOUNT_CREATED = "account_created"
    JOINED_ORGANIZATION = "joined_organization"
    LOGIN = "login"
    FAILED_LOGIN = "failed_login"
    TWO_FA_ENABLED = "two_fa_enabled"
    TWO_FA_DISABLED = "two_fa_disabled"
    PASSWORD_CHANGED = "password_changed"

class ReferenceType(StrEnum):
    ORGANIZATION = "organization"
    USER = "user"


#permission
SUPER_ADMIN_ONLY_PERMISSIONS: frozenset[str] = frozenset({
    "org.deactivate",
})

SEEDED_PERMISSION_SCOPE = "both"


# ── OTP ───────────────────────────────────────────────────────────────────────
# Plain integers — not StrEnum because these are numeric config values,
# not string identifiers stored in the DB or compared against token claims.

OTP_LENGTH = 6
# Digit count for all OTP codes.
# NIST SP 800-63B recommends a minimum of 6 digits for memorised secrets.
# Increase to 8 for higher-assurance flows (e.g. 2FA code reset) if needed.

OTP_TTL_MINUTES = 10
# How long a code stays valid after generation.
# 10 minutes is the TOTP window convention and short enough to limit
# the practical brute-force window on a 6-digit code.

EMAIL_VERIFICATION_TOKEN_TTL_DAYS = 7

OTP_MAX_ATTEMPTS = 5
# Wrong guesses allowed before the code is locked.
# After this many failures the record is marked used=True, forcing the


#media
MEDIA_TYPES = ("image", "video")


# Shared vocabulary for DailyCheckin.mood / energy_level / stress_level.
# Kept as a module-level tuple so the CheckConstraint and any app-layer
# validation can both reference the same source of truth.
CHECKIN_VOCABULARY = ("good", "bad", "stressed", "tired")
PRIORITY_VOCABULARY = (
    "reduce_stress",
    "build_energy",
    "improve_balance",
    "save_better",
    "stay_active",
    "feel_connected",
)

#engagement
ENGAGEMENT_METRIC_TYPES = ("participation", "checkins", "social", "productivity")


#Password validation
MIN_PASSWORD_LENGTH = 8
SPECIAL_CHAR_PATTERN = re.compile(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]")
UPPERCASE_PATTERN = re.compile(r"[A-Z]")
DIGIT_PATTERN = re.compile(r"[0-9]")

#branch_name
HEADQUARTERS_BRANCH_NAME = "Headquarters"

#challenge
CHALLENGE_STATUSES = ("upcoming", "active", "completed", "cancelled", "archived")
TARGET_TYPES = ("cumulative", "daily_minimum")
TRACKING_METHODS = ("automatic", "self_reported", "evidence_based", "platform_recorded")
SUBMISSION_REVIEW_STATUSES = ("pending", "approved", "rejected")

#chat
VALID_CONVERSATION_TYPES = {"club", "event"}

#Events
RSVP_STATUSES = ("invited", "accepted", "declined", "pending")
EVENT_STATUSES = ("scheduled", "ongoing", "completed", "cancelled")

#organization
INVITE_TTL_DAYS = 7
# Roles that only a super_admin or admin inviter may hand out. Anyone
# holding a branch-scoped member.invite grant (even a branch_manager)
# is blocked from inviting someone into these roles.
ESCALATION_RESTRICTED_ROLES = {RoleName.ADMIN, RoleName.BRANCH_MANAGER}

#wellbeings 
ASSESSMENT_STATUSES = ("pending", "completed", "reviewed")
BASELINE_DIMENSIONS = ("mood", "stress", "energy", "work_life_balance")
BASELINE_LEVELS = ("amazed", "stressed", "tired", "excited", "infuriated")
# Minimum number of distinct participants required before an aggregate
# wellbeing score is returned. Below this, individual scores could be
# effectively de-anonymized by process of elimination in a small
# branch/team — so the aggregate is suppressed entirely rather than
# shown with a misleading precision. This number is a product/privacy
# decision, not a technical one — 5 is a reasonable starting point but
# should be owned and revisited deliberately, not treated as settled.
ANONYMITY_THRESHOLD = 5


#notifications

NOTIFICATION_TYPES = (
    "liked_post",
    "commented_post",
    "new_message",
    "joined_club",
    "joined_department",
    "event_reminder",
    "challenge_completed",
    "challenge_daily_goal_met", 
    "announcement",
)

REFERENCE_TYPES = (
    "post",
    "comment",
    "message",
    "event",
    "challenge",
    "club",
    "department",
    "survey"

)

#users
USER_STATUSES = ("pending", "active", "inactive", "suspended")

#subscriptions
ACTIVE_STATUSES = ("trialing", "active")

#platform admin (Wellstaq staff — see app/models/admin_user.py)
ADMIN_ROLES = ("super_admin", "admin", "support")

ADMIN_PERMISSIONS = (
    "organizations.read", "organizations.manage",
    "individuals.read", "individuals.manage",
    "events.read", "events.manage",
    "support.read", "support.manage",
    "admins.manage",
    "audit.read",
)

# Mirrors docs/ADMIN_API_CONTRACT.md section 3 on the admin console repo
# exactly. super_admin isn't listed per-permission below — it implicitly
# holds every permission in ADMIN_PERMISSIONS, checked separately in
# app/auth/admin_dependencies.py rather than duplicated into this dict.
ADMIN_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "admin": (
        "organizations.read", "organizations.manage",
        "individuals.read", "individuals.manage",
        "events.read", "events.manage",
        "support.read", "support.manage",
        "audit.read",
    ),
    "support": (
        "organizations.read",
        "individuals.read",
        "events.read",
        "support.read", "support.manage",
    ),
}