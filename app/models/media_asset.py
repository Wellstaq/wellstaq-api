# app/models/media_asset.py
"""
Tracks every presigned upload issued via /storage/upload-url, from
'pending' (client got a URL, may or may not have used it) through
'attached' (a Post/Story/Challenge/etc row now references this object_key)
to 'deleted' (S3 object removed by a sweep job).

This is what lets the sweep jobs find and clean up:
  - uploads that were presigned but never attached to anything (abandoned uploads)
  - uploads that WERE attached, but the parent row was later deleted or
    the image was replaced (orphaned-on-delete)
without needing bespoke cleanup logic in every domain's delete path.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import UUIDPrimaryKeyMixin, TimestampMixin
from app.models.base import Base

DOMAINS = ("posts", "stories", "chat", "avatars", "challenges", "events", "clubs")
STATUSES = ("pending", "attached", "deleted")


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_assets"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String, nullable=False)
    object_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)

    # Polymorphic pointer to whatever row ends up using this asset.
    # Nullable until attach_media_asset() is called on successful create.
    attached_to_type: Mapped[str | None] = mapped_column(String, nullable=True)
    attached_to_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(f"domain IN {DOMAINS}", name="ck_media_assets_domain"),
        CheckConstraint(f"status IN {STATUSES}", name="ck_media_assets_status"),
    )