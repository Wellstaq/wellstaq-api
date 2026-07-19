from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LeaderboardEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    first_name: str
    last_name: str
    value: Decimal
    rank: int
    previous_rank: int | None
    org_rank: int
    previous_org_rank: int | None


class LeaderboardResponse(BaseModel):
    metric_type: str
    period_type: str
    period_start: date
    period_end: date
    scope: str  # "branch" | "org"
    items: list[LeaderboardEntryResponse]