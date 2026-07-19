"""
app/jobs/notifications/email.py

send_invite_email — sends the invite email for a newly created
organization_invites row. Blocked on an email provider decision.
"""

from typing import Any


async def send_invite_email(ctx: dict[str, Any], invite_id: str) -> None:
    """TODO: load the invite, build the {slug}/invite/{invite_code} URL,
    send via whatever email provider is chosen later."""
    pass