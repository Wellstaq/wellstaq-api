"""Enforce role tenant and platform-template scope.

Revision ID: 6c13d8e2a4f1
Revises: 91c8d77a6f24
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6c13d8e2a4f1"
down_revision: Union[str, None] = "91c8d77a6f24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = (
    "(is_system = true AND organization_id IS NULL AND branch_id IS NULL "
    "AND template_role_id IS NULL) OR "
    "(is_system = false AND organization_id IS NOT NULL)"
)


def upgrade() -> None:
    connection = op.get_bind()
    invalid_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM roles WHERE NOT (" + _CONSTRAINT + ")"
        )
    ).scalar_one()
    if invalid_count:
        raise RuntimeError(
            f"Cannot enforce role tenant scope: {invalid_count} invalid role row(s) exist. "
            "Platform templates must have is_system=true with no organization, branch, "
            "or template_role_id; every non-system role must have organization_id. "
            "Correct those rows explicitly, then rerun the migration."
        )

    op.create_check_constraint(
        "ck_role_tenant_scope_consistent", "roles", _CONSTRAINT
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_role_tenant_scope_consistent", "roles", type_="check"
    )
