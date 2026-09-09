"""enforce one pending checkout per organization

Revision ID: 91c8d77a6f24
Revises: 2db93fa31226
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "91c8d77a6f24"
down_revision: Union[str, None] = "2db93fa31226"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("is_recurring", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    # Preserve the newest pending checkout for each organization and make
    # older duplicates terminal before adding the invariant.
    op.execute(
        """
        UPDATE payment_transactions
        SET status = 'cancelled',
            failure_reason = 'Superseded while enforcing one pending checkout per organization.'
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY organization_id
                           ORDER BY created_at DESC, id DESC
                       ) AS pending_rank
                FROM payment_transactions
                WHERE status = 'pending'
            ) ranked
            WHERE pending_rank > 1
        )
        """
    )
    op.create_index(
        "uq_payment_transactions_one_pending_per_org",
        "payment_transactions",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_payment_transactions_one_pending_per_org",
        table_name="payment_transactions",
    )
    op.drop_column("payment_transactions", "is_recurring")
