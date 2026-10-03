"""Verified native-health activity sync.

Revision ID: e4b8197d4a21
Revises: b7a42d01e69c
"""

from alembic import op
import sqlalchemy as sa

revision = "e4b8197d4a21"
down_revision = "b7a42d01e69c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("activity_logs", sa.Column("source", sa.String(length=30), nullable=False, server_default="manual"))
    op.add_column("activity_logs", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "health_activity_syncs",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("branch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("activity_log_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("metric_type", sa.String(length=50), nullable=False),
        sa.Column("recorded_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("recording_method", sa.String(length=20), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=True),
        sa.Column("source_id", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("recording_method IN ('automatic', 'active')", name="ck_health_activity_sync_recording_method"),
        sa.ForeignKeyConstraint(["activity_log_id"], ["activity_logs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "external_id", name="uq_health_activity_sync_user_provider_external"),
    )
    op.create_index("ix_health_activity_syncs_organization_id", "health_activity_syncs", ["organization_id"])
    op.create_index("ix_health_activity_syncs_branch_id", "health_activity_syncs", ["branch_id"])
    op.create_index("ix_health_activity_syncs_user_id", "health_activity_syncs", ["user_id"])
    op.create_index("ix_health_activity_syncs_activity_log_id", "health_activity_syncs", ["activity_log_id"])
    op.create_index("ix_health_activity_sync_org_user_date", "health_activity_syncs", ["organization_id", "user_id", "recorded_date"])


def downgrade():
    op.drop_index("ix_health_activity_sync_org_user_date", table_name="health_activity_syncs")
    op.drop_index("ix_health_activity_syncs_activity_log_id", table_name="health_activity_syncs")
    op.drop_index("ix_health_activity_syncs_user_id", table_name="health_activity_syncs")
    op.drop_index("ix_health_activity_syncs_branch_id", table_name="health_activity_syncs")
    op.drop_index("ix_health_activity_syncs_organization_id", table_name="health_activity_syncs")
    op.drop_table("health_activity_syncs")
    op.drop_column("activity_logs", "is_verified")
    op.drop_column("activity_logs", "source")
