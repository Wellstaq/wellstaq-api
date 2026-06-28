"""
app/core/guards.py

Reusable async context manager for FastAPI route handlers.

Why this exists
───────────────
Every router in this project shares the same three failure modes:

  • OtpError  — expected business outcome (wrong code, locked out).
                Attempt-count increments MUST land in DB → commit before 4xx.
  • AppError  — any other domain error (AuthError, OrgError, etc.).
                State is invalid → rollback before 4xx.
  • Exception — unhandled crash.
                State is unknown → rollback + structured 500 log.

Without a shared guard, each endpoint repeats the same try/except/rollback
block. With it, the happy path is the only thing the endpoint body contains.

Usage
─────
    from app.core.guards import request_guard

    @router.post("/orgs")
    async def create_org(payload: ..., db: AsyncSession = Depends(get_db)):
        async with request_guard(db, "/orgs"):
            org = await org_service.create(db, payload)
            await db.commit()
            return org

Commit responsibility
─────────────────────
The CALLER commits on the happy path — the guard never commits for you.
The guard only commits for OtpError (to persist attempt-count writes) and
rolls back on all other exceptions. This keeps the router as the sole owner
of the transaction boundary, consistent with the project's service-layer contract.

Error type registration
────────────────────────
The guard is aware of two error families out of the box:

  • OtpError  — imported from app.services.otp_service
  • AppError  — the base class every domain error inherits from.
                AuthError, OrgError, BranchError, etc. all subclass AppError
                so a single except clause covers the whole domain.

If you add a new error type that needs different handling (e.g. a
PaymentError that must always notify a webhook before rolling back), add it
as a new except clause here rather than duplicating logic across routers.

Why a context manager, not a decorator
────────────────────────────────────────
A decorator wrapping an async def hides the function signature from FastAPI's
dependency injection resolver and breaks response_model inference at startup.
A context manager keeps the endpoint body explicit so FastAPI sees the real
signature unchanged.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Lazy imports ───────────────────────────────────────────────────────────────
# Imported inside the function to avoid circular imports at module load time.
# guards.py sits in app/core — importing from app/services at the top level
# would create a core → services → core cycle.
#
# The lazy import cost is negligible: Python caches modules after the first
# import, so the only overhead is a single dict lookup on subsequent calls.

def _get_error_classes():
    """Return (AppError, OtpError) without creating a circular import."""
    from app.services.errors import AppError         # base class for all domain errors
    from app.services.auth.otp_service import OtpError
    return AppError, OtpError


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _domain_to_http(exc: Any) -> HTTPException:
    """
    Convert any domain error that carries .status_code + .message to
    an HTTPException. Works for AuthError, OrgError, BranchError, etc.
    as long as they inherit from AppError and set those two attributes.
    """
    return HTTPException(
        status_code=getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST),
        detail=getattr(exc, "message", str(exc)),
    )


def _otp_to_http(exc: Any) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST),
        detail=getattr(exc, "message", str(exc)),
    )


def _internal_error(endpoint: str, **log_ctx) -> HTTPException:
    """
    Log a structured 500 and return a safe HTTPException.

    All keyword args end up in the log record's `extra` dict so that
    log aggregators (Datadog, Loki, CloudWatch Insights) can filter by
    endpoint, user_id, email, etc. without parsing message strings.

    Example:
        raise _internal_error("/auth/login", email="x@y.com", user_id="abc")

    Produces a log record with:
        {"endpoint": "/auth/login", "email": "x@y.com", "user_id": "abc"}
    """
    logger.error(
        "Unhandled exception in %s",
        endpoint,
        exc_info=True,
        extra={"endpoint": endpoint, **log_ctx},
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred.",
    )


# ── The guard ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def request_guard(
    db: AsyncSession,
    endpoint: str,
    **log_ctx,
):
    """
    Async context manager that handles all three failure modes for a route.

    Parameters
    ──────────
    db        — the AsyncSession for this request (used for rollback/commit)
    endpoint  — route path string, used in structured log output ("/auth/login")
    **log_ctx — arbitrary key/value pairs added to the 500 log record
                (e.g. email=payload.email, user_id=str(current_user.user_id))

    Failure modes handled
    ──────────────────────
    OtpError  → commit (attempt_count increments must survive) → 4xx
    AppError  → rollback → 4xx   (covers AuthError, OrgError, etc.)
    HTTPException → rollback → re-raise as-is
                (pre-flight 401s from _verify_google / _verify_apple pass through)
    Exception → rollback → structured 500 log → generic HTTPException

    The caller is responsible for db.commit() on the happy path.
    """
    AppError, OtpError = _get_error_classes()

    try:
        yield

    except OtpError as exc:
        # OtpError is an expected business outcome, not a crash.
        # attempt_count increments and lockout flags flushed by the service
        # MUST reach the DB — rolling back would silently erase them and
        # leave the door open for unlimited brute-force attempts.
        await db.commit()
        raise _otp_to_http(exc)

    except AppError as exc:
        await db.rollback()
        raise _domain_to_http(exc)

    except HTTPException:
        # Pre-flight helpers (_verify_google, _verify_apple) raise HTTPException
        # directly. Roll back any partial writes and let the 401 through unchanged.
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise _internal_error(endpoint, **log_ctx)