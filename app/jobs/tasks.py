"""
Task function stubs.

Every function here is a plain async function — ARQ calls them with
`ctx` (a dict holding shared resources like the Redis pool) as the
first argument, then whatever args were passed at enqueue time.

These are intentionally unimplemented (pass / TODO) until the
corresponding models and services exist. The point right now is the
wiring: worker.py already knows about every one of these, so filling
in the body later is the only step left, no registration plumbing.
"""

from typing import Any


async def compute_leaderboard_snapshots(ctx: dict[str, Any]) -> None:
    """
    Recompute leaderboard_snapshots for every organization.
    TODO: query activity_logs grouped by user/metric_type for the
    period, rank within each organization, write rows with
    previous_rank pulled from the prior snapshot for the up/down arrow.
    """
    pass


async def compute_kpi_snapshots(ctx: dict[str, Any]) -> None:
    """
    Recompute kpi_snapshots for every organization (total_departments,
    total_activity, events_created, total_clubs, etc).
    TODO: aggregate counts/sums per organization_id, write kpi_snapshots rows.
    """
    pass


async def compute_hashtag_trend_snapshots(ctx: dict[str, Any]) -> None:
    """
    Recompute hashtag_trend_snapshots on a 15-60 minute cadence.
    TODO: count posts per hashtag per organization within the rolling
    window, compute trend_score, write hashtag_trend_snapshots rows.
    """
    pass


async def compute_department_rank_snapshots(ctx: dict[str, Any]) -> None:
    """
    Recompute department_rank_snapshots for every organization.
    TODO: aggregate activity_logs for each department's members,
    rank departments within their organization, write snapshot rows.
    """
    pass


async def resize_thumbnail(ctx: dict[str, Any], object_key: str) -> None:
    """
    Download an uploaded feed image, resize it with Pillow for scroll
    performance, re-upload the thumbnail. Triggered by enqueuing this
    job right after a presigned upload completes — never done inline
    in the upload request itself.
    TODO: use app.core.storage to download/upload, Pillow to resize.
    """
    pass


async def send_invite_email(ctx: dict[str, Any], invite_id: str) -> None:
    """
    Send the invite email for a newly created organization_invites row.
    TODO: load the invite, build the {slug}/invite/{invite_code} URL,
    send via whatever email provider is chosen later.
    """
    pass


async def fanout_notification(
    ctx: dict[str, Any],
    organization_id: str,
    actor_id: str,
    notification_type: str,
    reference_id: str,
    reference_type: str,
    recipient_ids: list[str],
) -> None:
    """
    Write a notifications row for each recipient in recipient_ids.
    Run as a background job rather than inline so liking a popular
    post doesn't block the request on writing hundreds of rows.
    TODO: bulk-insert notifications rows once the model exists.
    """
    pass
