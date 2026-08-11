from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DepartmentRankResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department_id: str
    department_name: str
    branch_id: str
    rank: int | None
    total_activities: int | None
    avg_daily_steps: Decimal | None
    performance_score: Decimal | None
    period_start: date | None
    period_end: date | None


class DepartmentRankListResponse(BaseModel):
    items: list[DepartmentRankResponse]
