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

SUPER_ADMIN_ONLY_PERMISSIONS: frozenset[str] = frozenset({
    "org.deactivate",
})


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

# app/core/constants.py

WELLBEING_DIMENSIONS = ("mood", "energy", "stress", "work_life_balance")

WELLBEING_LEVELS = ("good", "bad", "stressed", "tired", "excited", "amazed", "infuriated")
# expand this list as your product defines more valid levels

WELLBEING_REASONS = ("work", "family", "health", "finances", "sleep", "love")
# reason is optional but if supplied, must be from this list