"""
Authentication routes: register, login, refresh.

These are intentionally stubbed for now — the User model doesn't
exist yet (per the current build-out plan), so these endpoints
return 501 Not Implemented until app/models/users.py and
app/services/auth_service.py are filled in.

The shape of the request/response is already correct so the frontend
can integrate against it early.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def register(payload: RegisterRequest):
    """Create a new user account. Not yet implemented — see app/models/users.py."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Registration is not implemented yet — User model pending.",
    )


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Authenticate with email (as username) + password, return JWT tokens.
    Not yet implemented — see app/models/users.py.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Login is not implemented yet — User model pending.",
    )
