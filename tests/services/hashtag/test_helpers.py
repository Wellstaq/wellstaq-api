"""Tests for hashtag normalization and extraction."""

import pytest

from app.services.hashtag._helpers import extract_hashtags, normalize_tag


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("#Wellness", "wellness"), ("  Movement  ", "movement"), ("###TEAM", "team")],
)
def test_normalize_tag(raw: str, expected: str):
    assert normalize_tag(raw) == expected


def test_extract_hashtags_normalizes_and_deduplicates():
    assert extract_hashtags("#Wellness and #wellness with #Team_One") == {
        "wellness",
        "team_one",
    }


@pytest.mark.parametrize("content", [None, "", "there are no tags here"])
def test_extract_hashtags_handles_absent_tags(content):
    assert extract_hashtags(content) == set()
