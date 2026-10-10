"""
app/models/admin_user.py

Wellstaq platform staff — the people who run the company, not an
organization's own employees. Deliberately a separate table from User,
per the admin console's own contract (docs/ADMIN_API_CONTRACT.md on the
frontend): "Admins are Wellstaq staff, not organization users. Use a
separate admin account table... and reject organization tokens on
/app-admin/*." An AdminUser can never also be an org User under the
same identity — they'd register a second account with a different
email if they also needed org-side access, same as any other platform
user would.

AdminSession mirrors UserDevice's refresh-token-hash-plus-session-id
pattern (see token_service.py / dependencies.py) so admin refresh
tokens rotate and can be individually revoked the same way.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ADMIN_ROLES
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AdminUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    # Null until an invited admin completes their invite-accept flow and
    # sets a password — see app/services/platform_admin/auth_service.py.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False, default="support")
    # active | invited | disabled. An invited admin has no usable
    # password yet; disabled is a soft-revoke short of deleting the row
    # (keeps audit-log actor references intact).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="invited")

    # 2FA is mandatory for every admin (see contract's "Security asks") —
    # same TOTP/email primitives as app/services/auth/two_factor_service.py,
    # just stored on this table instead of UserSettings.
    two_fa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    two_fa_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    two_fa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    invited_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["AdminSession"]] = relationship(
        "AdminSession", back_populates="admin", cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(f"role IN {ADMIN_ROLES!r}", name="ck_admin_users_role_valid"),
        CheckConstraint("status IN ('active', 'invited', 'disabled')", name="ck_admin_users_status_valid"),
    )

    def __repr__(self) -> str:
        return f"<AdminUser id={self.id} email={self.email} role={self.role} status={self.status}>"


class AdminSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per admin device/login — mirrors UserDevice. The access
    token's 'sid' claim points here; revoking a row invalidates that
    session on its next request (see app/auth/admin_dependencies.py)."""

    __tablename__ = "admin_sessions"

    admin_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    refresh_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    admin: Mapped["AdminUser"] = relationship("AdminUser", back_populates="sessions")

    def __repr__(self) -> str:
        return f"<AdminSession id={self.id} admin={self.admin_user_id} revoked={self.revoked}>"
