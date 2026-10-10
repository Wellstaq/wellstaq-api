"""
app/models/audit_entry.py

Append-only log of every platform-admin write action — see
docs/ADMIN_API_CONTRACT.md section 12 on the frontend for the exact
list of actions that must be recorded. No updated_at: an audit row is
never modified after creation, same reasoning as PaymentTransaction's
TimestampMixin-free design.

actor_admin_id is nullable with ondelete=SET NULL rather than CASCADE —
deleting an admin must never silently erase the historical record of
what they did.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin

AUDIT_TARGET_TYPES = ("organization", "user", "event", "admin", "support_ticket")


class AuditEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_entries"

    actor_admin_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # e.g. "organization.suspended", "admin.role_changed" — free string,
    # app-validated, matching PaymentTransaction.payment_method's
    # no-DB-enum convention for the same "new ones shouldn't need a migration" reason.
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Denormalized human-readable label (e.g. the org's name at the time
    # of the action) — survives the target row being renamed or deleted later.
    target_label: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
    )

    actor: Mapped["AdminUser"] = relationship("AdminUser", foreign_keys=[actor_admin_id])

    __table_args__ = (
        CheckConstraint(f"target_type IN {AUDIT_TARGET_TYPES!r}", name="ck_audit_entries_target_type_valid"),
        Index("ix_audit_entries_target", "target_type", "target_id"),
        Index("ix_audit_entries_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditEntry id={self.id} action={self.action} target={self.target_type}:{self.target_id}>"
