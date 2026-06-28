class AppError(Exception):
    """Base for all service-layer exceptions."""


class AuthError(AppError):
    """Raised by auth services for expected, user-facing failures."""


class OtpError(AppError):
    """Raised by OTP service for invalid/expired codes."""