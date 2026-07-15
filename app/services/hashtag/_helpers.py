"""
app/services/hashtag/_helpers.py
"""

from __future__ import annotations

import re

# Matches '#' followed by word characters — service-layer extraction only.
# The DB CheckConstraints (lowercase, non-empty) are the safety net; this
# regex + normalize_tag() is what keeps us from ever hitting them.
_HASHTAG_PATTERN = re.compile(r"#(\w+)")


def normalize_tag(raw: str) -> str:
    """Strip leading '#' (if present), lowercase, trim whitespace.
    Mirrors the DB CheckConstraints on Hashtag.tag_name exactly."""
    return raw.lstrip("#").strip().lower()


def extract_hashtags(content: str | None) -> set[str]:
    """Pull all #tags out of post content, normalized and deduplicated.
    Empty/whitespace-only matches are dropped (DB would reject them anyway,
    but no reason to attempt the insert)."""
    if not content:
        return set()

    tags = {normalize_tag(match) for match in _HASHTAG_PATTERN.findall(content)}
    return {t for t in tags if t}