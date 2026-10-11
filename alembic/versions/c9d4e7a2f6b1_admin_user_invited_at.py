"""Add admin_users.invited_at — tracks when an admin invite was last
issued, independent of created_at/updated_at, so expire_admin_invites
can sweep invites older than INVITE_TTL_DAYS without being confused by
a resurrected (re-invited) row's original creation time.

Revision ID: c9d4e7a2f6b1
Revises: a7e2c4f891bd
"""

from alembic import op
import sqlalchemy as sa

revision = "c9d4e7a2f6b1"
down_revision = "a7e2c4f891bd"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admin_users", sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("admin_users", "invited_at")
