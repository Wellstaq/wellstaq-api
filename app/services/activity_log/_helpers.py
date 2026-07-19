"""
app/services/activity_log/_helpers.py
"""

from __future__ import annotations

from datetime import datetime, timezone


def today_utc():
    # Same known simplification flagged on DailyCheckin and challenge
    # progress — server UTC calendar day, not the user's local day.
    # Revisit together, in one place, once per-user timezone storage
    # exists — don't fix this pipeline in isolation while the other two
    # (checkin, challenge progress) still use the same rule.
    return datetime.now(timezone.utc).date()