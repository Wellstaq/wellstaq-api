"""
app/routers/user_settings.py

Self-only — every route here acts on the caller's own account, except
forgot-password/reset-password which are logged-out flows by nature
(no current_user available yet).

Routes:
  GET   /user-settings/profile
  PATCH /user-settings/profile
  PUT   /user-settings/avatar
  POST  /user-settings/change-password
  POST  /auth/forgot-password
  POST  /auth/reset-password
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.user_setting import (
    AvatarUpdateRequest,
    AvatarUpdateResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ProfileResponse,
    ProfileUpdateRequest,
    ResetPasswordRequest,
)
from app.services.user_setting import (
    change_password,
    get_profile,
    request_password_reset,
    reset_password,
    update_avatar,
    update_profile,
)

router = APIRouter(prefix="/user-settings", tags=["user-settings"])


@router.get("/profile", response_model=ProfileResponse, status_code=status.HTTP_200_OK)
async def get_profile_route(
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> ProfileResponse:
    async with request_guard(db, "/user-settings/profile"):
        user = await get_profile(session=db, user_id=current_user.user_id)
        return ProfileResponse.model_validate(user)


@router.patch("/profile", response_model=ProfileResponse, status_code=status.HTTP_200_OK)
async def update_profile_route(
    body: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> ProfileResponse:
    async with request_guard(db, "/user-settings/profile"):
        user = await update_profile(
            session=db, user_id=current_user.user_id, first_name=body.first_name,
            last_name=body.last_name, country=body.country, state=body.state,
        )
        await db.commit()
        return ProfileResponse.model_validate(user)


@router.put("/avatar", response_model=AvatarUpdateResponse, status_code=status.HTTP_200_OK)
async def update_avatar_route(
    body: AvatarUpdateRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
) -> AvatarUpdateResponse:
    async with request_guard(db, "/user-settings/avatar"):
        avatar_url = await update_avatar(session=db, user_id=current_user.user_id, media_url=body.media_url)
        await db.commit()
        return AvatarUpdateResponse(avatar_url=avatar_url)


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password_route(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(get_current_user),
):
    async with request_guard(db, "/user-settings/change-password"):
        await change_password(
            session=db, user_id=current_user.user_id,
            current_password=body.current_password, new_password=body.new_password,
        )
        await db.commit()
        return {"message": "Password changed successfully."}


# ── Logged-out flows — separate prefix, no auth dependency ───────────────────

auth_password_router = APIRouter(prefix="/auth", tags=["user-settings"])


@auth_password_router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password_route(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/auth/forgot-password"):
        await request_password_reset(session=db, email=body.email)
        await db.commit()
        # Always returns the same success message regardless of whether
        # the email exists — see request_password_reset's docstring.
        return {"message": "If an account exists for this email, a reset link has been sent."}


@auth_password_router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password_route(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/auth/reset-password"):
        await reset_password(session=db, reset_token=body.reset_token, new_password=body.new_password)
        await db.commit()
        return {"message": "Password reset successfully."}