"""
Payment models — subscription plans, org subscriptions, and transactions.

Architecture principles:
- Three distinct concerns, three distinct tables:
    1. SubscriptionPlan   — global lookup (what plans exist)
    2. OrganizationSubscription — per-org state (what plan is this org on)
    3. PaymentTransaction — append-only ledger (every charge attempt)

- The database is NOT the source of truth for payment state — your
  payment gateway (e.g. Stripe) is. These tables store a snapshot of
  what the gateway tells you via webhooks. Always treat the gateway as
  authoritative and reconcile against it using the external_reference
  fields if your DB and the gateway diverge.

- organization_id belongs on every payment table without exception —
  organizations are the billing unit in a B2B SaaS, not users.

- ondelete="RESTRICT" on OrganizationSubscription.organization_id:
  you cannot delete an org that has an active subscription without
  cancelling it first in the gateway. This is intentional friction —
  an org should not vanish from your DB while the gateway still thinks
  they're subscribed.

- ondelete="CASCADE" on PaymentTransaction.organization_id: once an
  org is fully offboarded (subscription cancelled, gateway record
  closed), historical transaction rows can be wiped as part of a
  GDPR deletion request. Change to RESTRICT if your jurisdiction
  requires retaining financial records for a fixed period even after
  account closure — that's a legal/compliance decision, not a
  technical one.

- PaymentTransaction is append-only — rows are never updated. Status
  changes (e.g. a charge moving from "pending" to "succeeded") are new
  rows, not updates to existing ones. This gives you a full audit trail
  without needing soft deletes or updated_at on the transaction table.
  updated_at is therefore intentionally absent (no TimestampMixin here,
  only created_at).

- external_reference fields store the gateway's own IDs (e.g. Stripe's
  sub_xxx, cus_xxx, ch_xxx). Store these on every row so you can always
  reconcile or look up the source record in the gateway's dashboard
  without guessing.

- Currency is stored as an ISO 4217 uppercase code (e.g. "USD", "NGN",
  "GBP") — free string, validated at app layer, no DB enum so adding a
  new currency never requires a migration.

- amount is stored as an integer in the smallest currency unit
  (e.g. cents for USD, kobo for NGN) — the standard gateway convention.
  Never store amounts as floats (floating point arithmetic is not safe
  for money).
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SubscriptionPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Global lookup table — the plans your platform offers.

    Not org-scoped, same pattern as WellbeingDimension: shared taxonomy
    that every org references but no org owns.

    - billing_interval: "monthly" | "annual" — free string, app-validated.
    - price_kobo / price_cents: store in smallest unit per currency.
      For simplicity, one price column per plan is fine for now; if you
      need multi-currency pricing, that becomes a separate
      plan_prices table later.
    - max_employees: NULL means unlimited (enterprise/custom plans).
    - is_active: False = plan is retired, existing subs continue but
      new orgs cannot sign up for it.
    """

    __tablename__ = "subscription_plans"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. "monthly" | "annual"
    billing_interval: Mapped[str] = mapped_column(String(20), nullable=False)
    # Amount in smallest currency unit (e.g. kobo for NGN, cents for USD)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # ISO 4217 uppercase — e.g. "NGN", "USD", "GBP"
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # NULL = unlimited
    max_employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # False = retired plan, no new signups allowed
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Gateway's plan/price ID — e.g. Stripe price_xxx
    external_plan_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # ── Relationships ──────────────────────────────────────────────────────
    subscriptions: Mapped[list["OrganizationSubscription"]] = relationship(
        "OrganizationSubscription",
        back_populates="plan",
    )

    __table_args__ = (
        CheckConstraint(
            "billing_interval IN ('monthly', 'annual')",
            name="ck_subscription_plans_billing_interval",
        ),
        CheckConstraint("amount >= 0", name="ck_subscription_plans_amount_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<SubscriptionPlan id={self.id} name={self.name} interval={self.billing_interval}>"


class OrganizationSubscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One active subscription row per organization.

    Unique constraint on organization_id — an org is on exactly one
    plan at a time. To upgrade/downgrade, update this row (and sync
    with the gateway); don't create a new row.

    status lifecycle (gateway-driven, set via webhook):
      trialing → active → past_due → cancelled
      or:        active → cancelled  (immediate cancellation)

    - current_period_start / current_period_end: the active billing
      window. Updated on each successful renewal webhook.
    - cancelled_at: stamped when the sub moves to cancelled. The row
      is NOT deleted — historical record of when the org was on what
      plan is valuable for support and analytics.
    - trial_ends_at: NULL if no trial. Set at creation time if the
      plan has a trial period.
    - external_subscription_id: gateway's sub_xxx (Stripe) or equivalent.
    - external_customer_id: gateway's cus_xxx. Store here so you don't
      need to look it up from transactions when creating new charges.
    """

    __tablename__ = "organization_subscriptions"

    # ── FKs ───────────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ── Status ────────────────────────────────────────────────────────────
    # trialing | active | past_due | cancelled — app/webhook validated
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="trialing")

    # ── Billing window ────────────────────────────────────────────────────
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Gateway references ────────────────────────────────────────────────
    # e.g. Stripe sub_xxx
    external_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True,
    )
    # e.g. Stripe cus_xxx — store once, reuse for all future charges
    external_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True,
    )

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="subscription",
    )
    plan: Mapped["SubscriptionPlan"] = relationship(
        "SubscriptionPlan",
        back_populates="subscriptions",
    )
    transactions: Mapped[list["PaymentTransaction"]] = relationship(
        "PaymentTransaction",
        back_populates="subscription",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # One active plan per org at a time
        UniqueConstraint("organization_id", name="uq_organization_subscriptions_org"),
        CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'cancelled')",
            name="ck_organization_subscriptions_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<OrganizationSubscription org={self.organization_id} plan={self.plan_id} status={self.status}>"


class PaymentTransaction(Base, UUIDPrimaryKeyMixin):
    """
    Append-only ledger — one row per charge attempt, success, or failure.

    Never update rows in this table. Status changes are new rows.
    No updated_at — TimestampMixin is deliberately not used here;
    only created_at is needed for an immutable ledger entry.

    - amount in smallest currency unit (same convention as SubscriptionPlan).
    - status: succeeded | failed | refunded | pending
    - payment_method: card | bank_transfer | ussd | etc — free string,
      app-validated, no enum so adding a new method never needs a migration.
    - external_reference: gateway's charge/payment ID (e.g. Stripe ch_xxx
      or pi_xxx). Index this — you will query it from webhooks constantly.
    - failure_reason: NULL on success; set to gateway's decline code or
      error message on failure — essential for support troubleshooting.
    """

    __tablename__ = "payment_transactions"

    # ── FKs ───────────────────────────────────────────────────────────────
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organization_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Transaction data ───────────────────────────────────────────────────
    # Smallest currency unit (kobo, cents, etc.) — never use float for money
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # succeeded | failed | refunded | pending
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # card | bank_transfer | ussd | etc — free string, app-validated
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Gateway references ─────────────────────────────────────────────────
    # e.g. Stripe ch_xxx or pi_xxx — queried on every webhook
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # NULL on success — gateway's decline code or error message on failure
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Timestamp ─────────────────────────────────────────────────────────
    # No updated_at — this table is append-only, rows are never updated
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="transactions",
    )
    subscription: Mapped["OrganizationSubscription"] = relationship(
        "OrganizationSubscription",
        back_populates="transactions",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'refunded', 'pending')",
            name="ck_payment_transactions_status",
        ),
        CheckConstraint("amount > 0", name="ck_payment_transactions_amount_positive"),
        # Fast lookup: all transactions for an org in a date range
        Index("ix_payment_transactions_org_created_at", "organization_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<PaymentTransaction org={self.organization_id} amount={self.amount} status={self.status}>"