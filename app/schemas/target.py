"""
app/schemas/target.py
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TargetSetRequest(BaseModel):
    metric_type: str = Field(..., description="e.g. steps, distance")
    period_type: str = Field(..., description="'daily' | 'weekly' | 'monthly'")
    period: str = Field(..., description="Period label matching period_type, e.g. '2026-07', '2026-W30', '2026-07-25'")
    target_value: Decimal = Field(..., gt=0)


class TargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    branch_id: str
    metric_type: str
    period: str
    target_value: Decimal


class TargetListResponse(BaseModel):
    items: list[TargetResponse]


class TargetProgressResponse(BaseModel):
    branch_id: str
    metric_type: str
    period: str
    target_value: Decimal | None
    current_value: Decimal
    percent_complete: Decimal | None
    has_target: bool