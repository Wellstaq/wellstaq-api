"""
Shared SQLAlchemy declarative base and reusable mixins.

Every model file in app/models/ should import `Base` from here and
inherit from it. Mixins are opt-in — apply deliberately per table.

UUID Strategy:
    We use UUID v7 (time-ordered) instead of v4 (random) because:
    - Time-ordered UUIDs insert at the END of the B-tree index
    - Random UUIDs cause index fragmentation → slower inserts & queries
    - UUID v7 gives you both uniqueness AND chronological ordering for free
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid7() -> uuid.UUID:
    """
    Generate a time-ordered UUID v7.
    Monotonically increasing → fast B-tree inserts, no index fragmentation.
    """
    import time
    import os

    # 48-bit millisecond timestamp
    ts_ms = int(time.time() * 1000)
    ts_bytes = ts_ms.to_bytes(6, "big")

    # 10 random bytes for uniqueness
    rand_bytes = os.urandom(10)

    # Assemble 16 bytes
    raw = bytearray(ts_bytes + rand_bytes)

    # Set version (4 bits) = 7
    raw[6] = (raw[6] & 0x0F) | 0x70
    # Set variant = 10xx
    raw[8] = (raw[8] & 0x3F) | 0x80

    return uuid.UUID(bytes=bytes(raw))


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    """
    Time-ordered UUID v7 primary key.
    - Faster inserts than random UUID v4
    - Naturally sortable by creation time
    - Works on SQLite, PostgreSQL, MySQL — no dialect-specific types
    """

    id: Mapped[str] = mapped_column(
        String(36),          # stored as plain string — works on ALL databases
        primary_key=True,
        default=lambda: str(uuid7()),
    )


class TimestampMixin:
    """
    created_at + updated_at for tables that need edit history.
    Uses timezone-aware UTC timestamps.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),  # Python-side for SQLite compatibility
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SoftDeleteMixin:
    """
    deleted_at for tables where records should be recoverable.
    Query pattern: filter(Model.deleted_at == None) for active records.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )