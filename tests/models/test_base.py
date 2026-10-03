"""Tests for the shared SQLAlchemy base and model mixins."""

from datetime import datetime, timezone
from uuid import RFC_4122

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, uuid7


def test_uuid7_has_expected_version_and_variant():
    value = uuid7()

    assert value.version == 7
    assert value.variant == RFC_4122


def test_uuid7_embeds_current_millisecond_timestamp():
    before = int(datetime.now(timezone.utc).timestamp() * 1000)
    value = uuid7()
    after = int(datetime.now(timezone.utc).timestamp() * 1000)
    encoded_timestamp = int.from_bytes(value.bytes[:6], "big")

    assert before <= encoded_timestamp <= after


def test_reusable_mixins_do_not_create_tables_of_their_own():
    table_names = set(Base.metadata.tables)

    assert not hasattr(UUIDPrimaryKeyMixin, "__table__")
    assert not hasattr(TimestampMixin, "__table__")
    assert not hasattr(SoftDeleteMixin, "__table__")
    assert "uuid_primary_key_mixin" not in table_names
    assert "timestamp_mixin" not in table_names
    assert "soft_delete_mixin" not in table_names
