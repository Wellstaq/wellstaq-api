"""
app/schemas/auth.py

Pydantic schemas for auth request/response validation.


2FA notes
---------
Two-factor auth state lives on UserSettings (two_fa_enabled,
two_fa_method, two_fa_secret).  The login flow is:

  1. POST /auth/login  →  correct password
       • 2FA disabled  →  TokenResponse (full JWT pair)  ← normal path
       • 2FA enabled   →  TwoFaChallengeResponse (short-lived challenge token,
                          no JWT yet)
  2. POST /auth/2fa/verify  →  correct TOTP/SMS/email code + challenge token
       →  TokenResponse (full JWT pair)

Setup / disable (requires a valid access token):
  POST /auth/2fa/setup    →  enable 2FA, choose method, get TOTP secret
  POST /auth/2fa/disable  →  disable 2FA (requires current password)
"""

from typing import Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from pydantic import BaseModel, Field, field_validator
import re


# ── OTP / signup flow ──────────────────────────────────────────────────────────

class OtpRequestSchema(BaseModel):
    """Step 1 of signup — request an email OTP."""
    email: EmailStr


class OtpVerifySchema(BaseModel):
    """Step 2 of signup — verify the OTP and receive email_verification_token."""
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)


class OrgOwnerRegisterRequest(BaseModel):
    """
    Step 3 — self-serve org-owner signup.

    email_verification_token comes from /auth/signup/otp/verify and
    proves the email was OTP-verified. The email itself is recovered
    server-side from the token — it is NOT re-supplied here, preventing
    substitution attacks.
    """
    email_verification_token: str
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8)
    business_name: str = Field(..., min_length=1, max_length=255)
    phone_number: str = Field(..., min_length=7, max_length=20)

    # NEW fields (map directly to Organization columns)
    employee_count: int = Field(..., ge=1)
    organization_type: str = Field(..., min_length=1, max_length=100)
    work_model: str = Field(..., min_length=1, max_length=50)

    

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one digit.")
        return v


   
class InviteRegisterRequest(BaseModel):
    """Accept an invite and create a user account."""
    invite_code: str
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8)


# ── Login ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


# ── OAuth ──────────────────────────────────────────────────────────────────────

class OAuthRequest(BaseModel):
    """
    Frontend-first OAuth flow (Google or Apple).

    Google: name + avatar come from the verified ID token claims.
            first_name / last_name here are IGNORED for Google.
    Apple:  the ID token only carries sub + email. Apple sends the
            user's name exactly ONCE — in the native
            ASAuthorizationAppleIDCredential object on first login.
            The frontend MUST capture first_name / last_name at that
            moment and forward them here. On subsequent logins these
            may be omitted (user row already exists).
    """
    id_token: str
    invite_code: str | None = None
    first_name: str | None = None   # Apple first login only
    last_name: str | None = None    # Apple first login only


# ── 2FA — challenge (returned when login requires 2FA) ────────────────────────

class TwoFaChallengeResponse(BaseModel):
    """
    Returned instead of TokenResponse when 2FA is enabled.

    two_fa_challenge_token is a short-lived signed token (e.g. 5 min)
    that encodes the user_id + device meta. The client exchanges it —
    along with the TOTP/SMS/email code — at POST /auth/2fa/verify.

    No JWT pair is issued until 2FA is successfully verified.
    """
    requires_2fa: Literal[True] = True
    two_fa_method: str          # "totp" | "sms" | "email"
    two_fa_challenge_token: str


class TwoFaVerifyRequest(BaseModel):
    """Complete a 2FA challenge to receive the full JWT pair."""
    two_fa_challenge_token: str
    code: str = Field(..., min_length=6, max_length=8)


# ── 2FA — setup / disable (authenticated routes) ──────────────────────────────

class TwoFaSetupRequest(BaseModel):
    """
    Enable 2FA for the authenticated user.

    method="totp": server generates + returns a TOTP secret (for QR code).
                   Client must complete verify_totp before 2FA is activated.
    method="sms":  phone_number required; OTP sent via SMS.
    method="email": OTP sent to the user's registered email.
    """
    method: Literal["totp", "sms", "email"]
    phone_number: str | None = None  # required when method="sms"


class TwoFaSetupResponse(BaseModel):
    """
    Returned after setup initiation.

    totp_secret / totp_uri are only populated when method="totp";
    they are None for sms / email methods.

    The setup is NOT active until the user completes /auth/2fa/verify
    with a valid first code — this confirms they have the secret stored.
    """
    method: str
    totp_secret: str | None = None  # base32 TOTP secret (show in QR)
    totp_uri: str | None = None     # otpauth:// URI for QR code libraries


class TwoFaDisableRequest(BaseModel):
    """Disable 2FA. Requires the current password as confirmation."""
    password: str


# ── Token responses ────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """Full JWT pair — returned on login (no 2FA) and after 2FA verify."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    """Access token only — returned by /auth/refresh (token not rotated)."""
    access_token: str
    token_type: str = "bearer"


# ── /me ───────────────────────────────────────────────────────────────────────

class UserMeResponse(BaseModel):
    user_id: str
    email: str
    first_name: str
    last_name: str
    role: str | None            # None when no role has been assigned
    organization_id: str
    branch_id: str | None
    permissions: list[str]
    avatar_url: str | None      # set for OAuth users with a profile photo
    two_fa_enabled: bool        # mirrors UserSettings.two_fa_enabled
    two_fa_method: str | None   # "totp" | "sms" | "email" | None







# ── Vocabulary ─────────────────────────────────────────────────────────────────

CheckinValue = Literal["amazed", "stressed", "tired", "excited", "infuriated"]

CheckinReason = Literal[
    "work", "family", "breakup", "sleep",
    "social", "food", "love", "exams", "others",
]

PriorityValue = Literal[
    "reduce_stress", "build_energy", "improve_balance",
    "save_better", "stay_active", "feel_connected",
]

WellbeingDimension = Literal["mood", "energy", "stress", "work_life_balance"]

_REQUIRED_DIMENSIONS: frozenset[str] = frozenset(
    {"mood", "energy", "stress", "work_life_balance"}
)


# ── Baseline entry — maps directly to one UserWellbeingBaseline row ────────────

class BaselineDimensionSchema(BaseModel):
    """One UserWellbeingBaseline row."""
    dimension: WellbeingDimension
    level: CheckinValue
    reason: CheckinReason


# ── Baseline collection — all 4 dimensions together ───────────────────────────

class WellbeingBaselineSchema(BaseModel):
    """
    All 4 wellbeing dimensions captured at registration.
    Becomes 4 UserWellbeingBaseline rows — one per entry.

    Reused later for the owner's post-login onboarding route.
    """
    entries: list[BaselineDimensionSchema] = Field(..., min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "WellbeingBaselineSchema":
        provided = [e.dimension for e in self.entries]

        # All 4 required
        missing = _REQUIRED_DIMENSIONS - set(provided)
        if missing:
            raise ValueError(f"Missing dimensions: {sorted(missing)}")

        # No duplicates
        if len(provided) != len(set(provided)):
            seen, dupes = set(), set()
            for d in provided:
                (dupes if d in seen else seen).add(d)
            raise ValueError(f"Duplicate dimensions: {sorted(dupes)}")

        return self


# ── Priority item ──────────────────────────────────────────────────────────────

class PriorityItemSchema(BaseModel):
    priority: PriorityValue
    rank: int = Field(..., ge=1, le=6)


# ── Invite OTP — Step 1 ────────────────────────────────────────────────────────

class InviteOtpRequestSchema(BaseModel):
    invite_code: str = Field(..., min_length=1)


# ── Invite OTP — Step 2 ────────────────────────────────────────────────────────

class InviteOtpVerifySchema(BaseModel):
    invite_code: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6)


# ── Invite registration — Step 3 ───────────────────────────────────────────────

class InviteRegisterRequest(BaseModel):
    invite_verification_token: str

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8)

    country: str = Field(..., min_length=2, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)

    # 4 UserWellbeingBaseline rows
    baseline: WellbeingBaselineSchema

    # 1–6 UserPriority rows
    priorities: list[PriorityItemSchema] = Field(..., min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_priorities(self) -> "InviteRegisterRequest":
        ranks = [p.rank for p in self.priorities]
        if len(ranks) != len(set(ranks)):
            raise ValueError("Priority ranks must be unique.")
        names = [p.priority for p in self.priorities]
        if len(names) != len(set(names)):
            raise ValueError("Each priority type can only appear once.")
        return self