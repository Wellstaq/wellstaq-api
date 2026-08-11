"""Harden payment transaction state and gateway reference integrity.

Revision ID: 20260811_01
Revises: None
"""

from alembic import op

revision = "20260811_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("payment_transactions", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_payment_transactions_status", type_="check")
        batch_op.create_check_constraint(
            "ck_payment_transactions_status",
            "status IN ('succeeded', 'failed', 'refunded', 'pending', 'cancelled')",
        )
        batch_op.create_unique_constraint(
            "uq_payment_transactions_external_reference",
            ["external_reference"],
        )


def downgrade() -> None:
    op.execute(
        "UPDATE payment_transactions SET status='failed', "
        "failure_reason=COALESCE(failure_reason, 'Checkout was cancelled.') "
        "WHERE status='cancelled'"
    )
    with op.batch_alter_table("payment_transactions", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_payment_transactions_external_reference", type_="unique")
        batch_op.drop_constraint("ck_payment_transactions_status", type_="check")
        batch_op.create_check_constraint(
            "ck_payment_transactions_status",
            "status IN ('succeeded', 'failed', 'refunded', 'pending')",
        )
