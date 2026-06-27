"""
app/services/errors.py

Base error classes shared across all service modules.

Every domain-specific error (AuthError, OrgError, BranchError, etc.)
inherits from AppError so that app/core/guards.py can catch the whole
family with a single except clause — without needing to know about
every individual error type.

Usage
─────
Define domain errors like this:

    # app/services/auth_service.py
    from app.services.errors import AppError

    class AuthError(AppError):
        pass   # inherits __init__, status_code, message — nothing else needed

    # app/services/org_service.py
    from app.services.errors import AppError

    class OrgError(AppError):
        pass

Then raise them identically:

    raise AuthError("Invalid token.", status.HTTP_401_UNAUTHORIZED)
    raise OrgError("Org not found.", status.HTTP_404_NOT_FOUND)

Both are caught by a single `except AppError` in request_guard().

OtpError is intentionally NOT a subclass of AppError — it requires a
commit() rather than a rollback(), so it must be caught separately.
See app/core/guards.py for the reasoning.
"""

from __future__ import annotations

from fastapi import status


class AppError(Exception):
    """
    Base class for all domain errors raised by service functions.

    Attributes
    ──────────
    message     — human-readable detail forwarded to the HTTP response body
    status_code — HTTP status code the router should return

    Subclass and raise — never instantiate AppError directly.
    """

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)