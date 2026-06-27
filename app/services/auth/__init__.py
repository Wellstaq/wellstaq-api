"""
app/services/auth/__init__.py

Public API for the auth service package.

Re-exports every name that app/routers/auth.py imports so the router's
import statements don't change after the split:

    from app.services.auth import (
        AuthError, OtpError,
        check_business_name_available, check_phone_available,
        disable_two_fa, get_me, initiate_two_fa_setup,
        invite_register, logout, oauth_login, org_owner_signup,
        password_login, refresh_access_token,
        request_invite_otp, request_otp,
        verify_invite_otp, verify_otp, verify_two_fa_challenge,
    )
"""

from app.services.errors import AuthError

# Signup
from .signup_service import org_owner_signup

# Availability checks — re-exported directly from organization_service,
# no thin wrapper needed (the router just calls them, not service logic)
from .organization_service import (
    check_business_name_available,
    check_phone_available,
)

# Invite
from .invite_service import (
    invite_register,
    request_invite_otp,
    verify_invite_otp,
)

# Login (password only)
from .login_service import password_login

# OAuth (account-linking login — separate from password login)
from .oauth_service import oauth_login

# Session
from .session_service import (
    get_me,
    logout,
    refresh_access_token,
)

# 2FA — delegated to two_factor_service, re-exported for router convenience
from .two_factor_service import (
    disable_two_fa,
    initiate_two_fa_setup,
    verify_two_fa_challenge,
)

# OTP — re-exported so the router can import OtpError from one place
from .services.otp_service import (
    OtpError,
    request_otp,
    verify_otp,
)

__all__ = [
    # Errors
    "AuthError",
    "OtpError",
    # Signup
    "org_owner_signup",
    # Availability checks
    "check_business_name_available",
    "check_phone_available",
    # Invite
    "invite_register",
    "request_invite_otp",
    "verify_invite_otp",
    # Login
    "password_login",
    "oauth_login",
    # Session
    "get_me",
    "logout",
    "refresh_access_token",
    # 2FA
    "disable_two_fa",
    "initiate_two_fa_setup",
    "verify_two_fa_challenge",
    # OTP
    "request_otp",
    "verify_otp",
]