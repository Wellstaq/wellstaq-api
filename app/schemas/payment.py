# app/schemas/payment.py
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    plan_id: str
    status: str
    auto_renew: bool  # new
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


class PaymentVerificationResponse(BaseModel):
    transaction: TransactionResponse
    provider_status: str
    reconciled: bool

class CheckoutRequest(BaseModel):
    plan_id: str = Field(..., description="The plan to check out into — e.g. the id of the 'Standard' plan.")
    callback_url: str | None = None
    auto_renew: bool = Field(default=False, description="If true, saves the payment method and auto-charges every renewal period. If false, this is a one-time charge only — no future charges without the customer explicitly initiating checkout again.")

class AutoRenewUpdateRequest(BaseModel):
    enabled: bool

class CheckoutResponse(BaseModel):
    authorization_url: str
    reference: str
    access_code: str | None

class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    billing_interval: str
    amount: int
    currency: str
    features: dict


class PlanListResponse(BaseModel):
    items: list[PlanResponse]
