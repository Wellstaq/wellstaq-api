"""
app/jobs/notifications/notification.py

fanout_notification — writes a notification row for each recipient in
recipient_ids, run as a background job rather than inline so a single
action (e.g. an org-wide announcement) doesn't block the request on
writing many rows.

Not yet wired to any trigger — nothing currently enqueues this job.
Distinct from the inline, synchronous create_notification() used by
like_post/add_comment for the single-recipient case.
"""

from typing import Any


async def fanout_notification(
    ctx: dict[str, Any],
    organization_id: str,
    actor_id: str,
    notification_type: str,
    reference_id: str,
    reference_type: str,
    recipient_ids: list[str],
) -> None:
    """TODO: bulk-insert notification rows, one per recipient_id, reusing
    the same validation create_notification() already does (see
    app/services/notification/create_notification.py) rather than
    duplicating it — likely calls that function in a loop, or a bulk
    insert built from the same shape."""
    pass