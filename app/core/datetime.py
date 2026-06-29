from datetime import datetime, timezone

def ensure_utc(dt: datetime) -> datetime:
    """
    Normalize a datetime to UTC.

    - Naive datetimes are assumed to already represent UTC (useful for SQLite).
    - Aware datetimes are converted to UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)