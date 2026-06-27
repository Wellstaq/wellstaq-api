"""
app/services/auth/errors.py

AuthError — the single exception type raised by all auth service functions.
Routers catch it and map to HTTP status codes via request_guard().
"""


class AuthError(Exception):
    pass