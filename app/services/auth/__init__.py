"""
app/services/auth/__init__.py

Re-exports the public surface of the auth service package so callers
can do:
    from app.services.auth import password_login, AuthError
instead of reaching into individual submodules.
"""

from .auth_service import (
    AuthError,
    org_owner_signup,
    request_invite_otp,
    verify_invite_otp,
    invite_register,
    password_login,
    oauth_login,
    refresh_access_token,
    logout,
)

from .organization_service import (
    slugify,
    unique_slug,
    check_business_name_available,
    check_phone_available,
    get_system_role,
    assign_role,
    seed_default_permissions,
)

from .otp_service import (
    OtpError,
    request_otp,
    verify_otp,
)

from .token_service import (
    load_user_permissions,
    build_claims,
    token_pair_response,
    access_only_response,
    two_fa_challenge_response,
    issue_tokens,
    rotate_refresh_token,
    revoke_device,
)

from .two_factor_service import (
    generate_totp_secret,
    totp_uri,
    verify_totp,
    issue_two_fa_challenge,
    initiate_two_fa_setup,
    confirm_two_fa_setup,
    verify_two_fa_challenge,
    disable_two_fa,
)

__all__ = [
    # auth_service
    "AuthError", "org_owner_signup", "request_invite_otp", "verify_invite_otp",
    "invite_register", "password_login", "oauth_login", "refresh_access_token", "logout",
    # organization_service
    "slugify", "unique_slug", "check_business_name_available",
    "check_phone_available", "get_system_role", "assign_role", "seed_default_permissions",
    # otp_service
    "OtpError", "request_otp", "verify_otp",
    # token_service
    "load_user_permissions", "build_claims", "token_pair_response",
    "access_only_response", "two_fa_challenge_response", "issue_tokens",
    "rotate_refresh_token", "revoke_device",
    # two_factor_service
    "generate_totp_secret", "totp_uri", "verify_totp", "issue_two_fa_challenge",
    "initiate_two_fa_setup", "confirm_two_fa_setup", "verify_two_fa_challenge",
    "disable_two_fa",
]