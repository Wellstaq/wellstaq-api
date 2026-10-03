"""Tests for provider-neutral storage path helpers."""

from uuid import UUID

import pytest

from app.services.storage._helpers import (
    build_object_key,
    content_type_to_extension,
    extract_object_key,
)


def test_build_object_key_is_tenant_scoped_and_random():
    first = build_object_key(domain="avatars", organization_id="org-1", extension="png")
    second = build_object_key(domain="avatars", organization_id="org-1", extension="png")

    assert first != second
    prefix, organization_id, filename = first.split("/")
    assert prefix == "avatars"
    assert organization_id == "org-1"
    assert filename.endswith(".png")
    UUID(filename.removesuffix(".png"))


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
        ("image/gif", "gif"),
        ("video/mp4", "mp4"),
        ("video/quicktime", "mov"),
        ("video/webm", "webm"),
        ("application/pdf", "bin"),
    ],
)
def test_content_type_to_extension(content_type: str, extension: str):
    assert content_type_to_extension(content_type) == extension


def test_extract_object_key_accepts_cdn_and_s3_urls(monkeypatch):
    monkeypatch.setattr(
        "app.services.storage._helpers.settings.STORAGE_CDN_DOMAIN",
        "assets.example.com",
    )

    assert extract_object_key("https://assets.example.com/posts/org/a.png") == "posts/org/a.png"
    assert extract_object_key("https://bucket.s3.amazonaws.com/posts/org/a.png") == "posts/org/a.png"


@pytest.mark.parametrize("value", [None, "", "https://example.com/not-storage/a.png"])
def test_extract_object_key_rejects_unrecognized_urls(value):
    assert extract_object_key(value) is None
