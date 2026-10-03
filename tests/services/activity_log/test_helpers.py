"""Tests for activity-log date helpers."""

from datetime import datetime, timezone

from app.services.activity_log._helpers import today_utc


def test_today_utc_uses_the_utc_calendar_day():
    before = datetime.now(timezone.utc).date()
    result = today_utc()
    after = datetime.now(timezone.utc).date()

    assert result in {before, after}
