"""
FastAPI dependencies for protecting routes.

Add `current_user_id: str = Depends(get_current_user_id)` (or the
fuller `get_current_user`, once the User model exists) to any route
that should be blocked for unauthenticated requests.

Until the User model is wired in (see app/models/users.py), this
file only resolves the user id out of the token — it does not yet
load the full user from the database. That hookup happens once the
first model lands.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.jwt import TokenError, decode_token

# Tells FastAPI/OpenAPI where clients should POST to get a token.
# This route will be implemented in app/routers/auth.py.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user_id(token: str = Depends(oauth2_scheme)) -> str:
    """
    Decode the bearer token and return the user id (subject).

    Raises 401 if the token is missing, malformed, expired, or not
    an access token. FastAPI runs this automatically for any route
    that depends on it, before the route body ever executes — so an
    unauthenticated request never reaches protected business logic.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise credentials_exception from exc

    if payload.get("type") != "access":
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    return user_id
