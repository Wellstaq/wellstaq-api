"""
app/services/errors.py

Base exception hierarchy for all service-layer code.

Every service exception carries:
  - message       — user-facing error text
  - status_code   — the HTTP status the router should map this to

Routers/guards catch these and convert them into HTTPException responses
without needing to know which specific service raised them.
"""

from fastapi import status


class AppError(Exception):
    """
    Base for all service-layer exceptions.

    message      — user-facing error text, safe to return in an HTTP response
    status_code  — the HTTP status code the router/guard should respond with
    """
    def __init__(self, message: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthError(AppError):
    """Raised by auth services for expected, user-facing failures."""


class OtpError(AppError):
    """Raised by OTP service for invalid/expired codes."""