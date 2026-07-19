"""
app/jobs/analytics/kpi.py

compute_kpi_snapshots — recomputes branch-level KPISnapshot rows.

Migration status: stub only — real logic not yet implemented, and still
blocked on an unresolved design question: whether org-wide figures
(total_departments, total_activity, events_created, total_clubs) are
computed per-branch-then-rolled-up, or need a different shape entirely.
Needs a decision before implementation starts.
"""

from typing import Any


async def compute_kpi_snapshots(ctx: dict[str, Any]) -> None:
    """
    TODO: aggregate counts/sums per organization_id (or per-branch, see
    module docstring's open question), write KPISnapshot rows.
    """
    pass