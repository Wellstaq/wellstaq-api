"""
app/jobs/helpers/scoring.py

Shared scoring primitives. These are generic enough to be reused by
leaderboard/department-rank scoring once those are built — landing them
here now (rather than inline in engagement.py) since compute_engagement_logs
is the first consumer, but the shape is intentionally domain-agnostic.
"""

from __future__ import annotations


def cap_score(count: float, ceiling: float) -> float:
    """Converts a raw count into a 0-100 score, capped at 100 once the
    count reaches ceiling. E.g. cap_score(3, 5) -> 60.0, cap_score(9, 5) -> 100.0."""
    if ceiling <= 0:
        return 0.0
    return min(100.0, (count / ceiling) * 100.0)


def trend_direction(current: float, previous: float | None) -> str | None:
    """None if there's no prior value to compare against (first-ever
    computation for this user/metric_type) — never defaults to 'flat',
    since that would misrepresent "no history" as "no change"."""
    if previous is None:
        return None
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"