"""
app/schemas/kpi_snapshot.py
"""

from decimal import Decimal
from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class KPISnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    branch_id: str
    metric_name: str
    period_start: date
    period_end: date
    value: Decimal


class KPISnapshotListResponse(BaseModel):
    items: list[KPISnapshotResponse]


class OrgWideKPIPoint(BaseModel):
    """One derived org-wide data point — SUM(value) across every branch
    that has a snapshot for this date. Not stored — see KPISnapshot's
    model docstring."""
    period_start: date
    period_end: date
    value: Decimal
    branch_count: int = Field(..., description="How many branches contributed to this total.")


class OrgWideKPIResponse(BaseModel):
    organization_id: str
    metric_name: str
    points: list[OrgWideKPIPoint]


class KPITrend(BaseModel):
    value: float | None = None
    change: float | None = None


class KPIOverviewSummary(BaseModel):
    average_daily_steps: KPITrend
    health_score: KPITrend
    active_employees: KPITrend
    challenges_won: KPITrend


class KPIChartPoint(BaseModel):
    name: str
    actual: float | None = None
    target: float | None = None
    steps: float | None = None


class KPIDistribution(BaseModel):
    name: str
    value: float
    color: str


class KPIDepartment(BaseModel):
    name: str
    branch_name: str | None = None
    engagement: float


class KPIPerformer(BaseModel):
    user_id: str
    first_name: str
    last_name: str
    avatar_url: str | None = None
    steps: float
    score: float
    rank: int


class KPIOverviewResponse(BaseModel):
    period: str
    scope: str
    branch_id: str | None = None
    summary: KPIOverviewSummary
    monthly_steps: list[KPIChartPoint] = Field(default_factory=list)
    health_distribution: list[KPIDistribution] = Field(default_factory=list)
    department_performance: list[KPIDepartment] = Field(default_factory=list)
    weekly_activity: list[KPIChartPoint] = Field(default_factory=list)
    top_performers: list[KPIPerformer] = Field(default_factory=list)
