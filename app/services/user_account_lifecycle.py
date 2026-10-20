"""Lifecycle actions for organization users.

Deletion is deliberately implemented as anonymization: the User row and its
foreign-key relationships remain available to historical records, while login
access and personally identifying account fields are removed.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Branch,
    MobilePushToken,
    Organization,
    User,
    UserAuthProvider,
    UserDevice,
    UserPermission,
    UserRoleAssignment,
    UserSettings,
)


DELETED_EMAIL_DOMAIN = "deleted.wellstaq.invalid"


def is_deleted_user(user: User) -> bool:
    return user.email.endswith(f"@{DELETED_EMAIL_DOMAIN}")


async def get_user_for_lifecycle(
    *, session: AsyncSession, user_id: str, organization_id: str | None = None,
) -> tuple[User, Organization]:
    query = (
        select(User, Organization)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.id == user_id)
    )
    if organization_id is not None:
        query = query.where(User.organization_id == organization_id)
    row = (await session.execute(query)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return row


def ensure_not_owner(*, user: User, organization: Organization) -> None:
    if organization.owner_id == user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The organization owner cannot be deleted.",
        )


async def _revoke_access(*, session: AsyncSession, user_id: str) -> None:
    await session.execute(
        update(UserDevice)
        .where(UserDevice.user_id == user_id)
        .values(revoked=True, refresh_token_hash=None)
    )
    await session.execute(
        update(MobilePushToken)
        .where(MobilePushToken.user_id == user_id)
        .values(is_active=False)
    )


async def suspend_user(*, session: AsyncSession, user: User, organization: Organization) -> None:
    if is_deleted_user(user):
        raise HTTPException(status.HTTP_409_CONFLICT, "A deleted user cannot be suspended.")
    user.status = "suspended"
    await _revoke_access(session=session, user_id=user.id)
    await session.flush()


async def reinstate_user(*, session: AsyncSession, user: User, organization: Organization) -> None:
    if is_deleted_user(user):
        raise HTTPException(status.HTTP_409_CONFLICT, "A deleted user cannot be reinstated.")
    if user.status != "suspended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only suspended users can be reinstated.")
    user.status = "active"
    await session.flush()


async def anonymize_user(*, session: AsyncSession, user: User, organization: Organization) -> None:
    ensure_not_owner(user=user, organization=organization)
    if is_deleted_user(user):
        return

    await _revoke_access(session=session, user_id=user.id)
    await session.execute(delete(UserAuthProvider).where(UserAuthProvider.user_id == user.id))
    await session.execute(update(Branch).where(Branch.manager_id == user.id).values(manager_id=None))
    await session.execute(delete(UserPermission).where(UserPermission.user_id == user.id))
    await session.execute(delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))
    await session.execute(
        update(UserSettings)
        .where(UserSettings.user_id == user.id)
        .values(
            public_profile=False,
            show_wellness_to_team=False,
            email_notifications=False,
            push_notifications=False,
            challenge_reminders=False,
            two_fa_enabled=False,
            two_fa_method=None,
            two_fa_secret=None,
        )
    )

    user.first_name = "Deleted"
    user.last_name = "User"
    user.email = f"deleted-{user.id}@{DELETED_EMAIL_DOMAIN}"
    user.phone_number = None
    user.avatar_url = None
    user.country = None
    user.state = None
    user.hashed_password = None
    user.role_id = None
    user.status = "inactive"
    await session.flush()
