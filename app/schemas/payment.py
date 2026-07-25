# app/schemas/payment.py
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    plan_id: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    cancelled_at: datetime | None


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    amount: int
    currency: str
    status: str
    payment_method: str | None
    created_at: datetime
    external_reference:str

class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int
    offset: int
    limit: int

class CheckoutRequest(BaseModel):
    callback_url: str | None = None


class CheckoutResponse(BaseModel):
    authorization_url: str
    reference: str
    access_code: str | None