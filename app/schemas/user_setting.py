"""
app/schemas/user_setting.py
"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import Literal


class PreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email_notifications: bool
    push_notifications: bool
    challenge_reminders: bool
    public_profile: bool
    show_activity: bool
    theme: Literal["light", "dark"]


class PreferencesUpdateRequest(BaseModel):
    email_notifications: bool | None = None
    push_notifications: bool | None = None
    challenge_reminders: bool | None = None
    public_profile: bool | None = None
    show_activity: bool | None = None
    theme: Literal["light", "dark"] | None = None


# ── Avatar ───────────────────────────────────────────────────────────────────

class AvatarUpdateRequest(BaseModel):
    media_url: str = Field(..., max_length=2048)


class AvatarUpdateResponse(BaseModel):
    avatar_url: str


# ── Profile ──────────────────────────────────────────────────────────────────

class ProfileUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    first_name: str
    last_name: str
    email: str
    avatar_url: str | None
    country: str | None
    state: str | None


# ── Password change (while logged in) ────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── Forgot / reset password (logged out) ─────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str
