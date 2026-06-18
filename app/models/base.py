"""
Shared SQLAlchemy declarative base and reusable mixins.

Every model file in app/models/ should import `Base` from here and
inherit from it. Mixins like TimestampMixin are opt-in per table —
not every table needs updated_at/deleted_at, so apply them
deliberately rather than on everything.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    """Standard UUID primary key, used by virtually every table in this schema."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """created_at + updated_at, for tables that need edit history (not join tables)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    """deleted_at, for tables where records should be recoverable rather than hard-deleted."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
