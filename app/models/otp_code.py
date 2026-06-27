"""
app/models/otp_code.py

OTP code model — used for email verification and email/SMS 2FA challenges.
Codes are single-use and short-lived (default 10 min).
"""

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy import Boolean, DateTime, String, Integer
from sqlalchemy.orm import Mapped, mapped_column

"""
    This table is intentionally created for development purpose only,
    when the app is ready for  Production it'll be permanently erased...
    Redis is the final goal for production otp storage.
"""
class OtpCode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "otp_codes"

    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")