"""
app/services/target/_helpers.py
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch
from app.models.snapshot_target import Target

PERIOD_TYPES = ("daily", "weekly", "monthly")


def build_period_label(*, period_type: str, reference_date: date) -> str:
    """
    Generates the SAME period label convention used for
    LeaderboardSnapshot's period_start/period_end grouping, but as a
    single free-form string (matching Target.period's existing shape),
    so a Target's period and a Leaderboard's period line up and can be
    compared meaningfully.

    daily:   "2026-07-25"
    weekly:  "2026-W30"        (ISO week)
    monthly: "2026-07"
    """
    if period_type == "daily":
        return reference_date.isoformat()
    if period_type == "weekly":
        iso_year, iso_week, _ = reference_date.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period_type == "monthly":
        return f"{reference_date.year}-{reference_date.month:02d}"
    raise ValueError(f"Invalid period_type '{period_type}'.")


def period_date_range(*, period_type: str, period: str) -> tuple[date, date]:
    """Inverse of build_period_label — given a period label, returns the
    actual date range it covers, needed to query ActivityLog."""
    if period_type == "daily":
        d = date.fromisoformat(period)
        return d, d

    if period_type == "weekly":
        year_str, week_str = period.split("-W")
        year, week = int(year_str), int(week_str)
        start = date.fromisocalendar(year, week, 1)
        return start, start + timedelta(days=6)

    if period_type == "monthly":
        year_str, month_str = period.split("-")
        year, month = int(year_str), int(month_str)
        start = date(year, month, 1)
        end = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        return start, end

    raise ValueError(f"Invalid period_type '{period_type}'.")


async def validate_branch(*, session: AsyncSession, organization_id: str, branch_id: str) -> None:
    result = await session.execute(
        select(Branch).where(Branch.id == branch_id, Branch.organization_id == organization_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found or does not belong to this organization.")


async def get_target_or_404(*, session: AsyncSession, organization_id: str, target_id: str) -> Target:
    result = await session.execute(
        select(Target).where(Target.id == target_id, Target.organization_id == organization_id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found.")
    return target