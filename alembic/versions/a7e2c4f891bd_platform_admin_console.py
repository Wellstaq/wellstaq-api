"""Platform admin console: AdminUser/AdminSession, AuditEntry,
PlatformEvent (+ org/participant join tables), Organization suspension
reason, and OrganizationSubscription platform_comped free-access flag.

Revision ID: a7e2c4f891bd
Revises: f3a9c1d8e2b7
"""

from alembic import op
import sqlalchemy as sa

revision = "a7e2c4f891bd"
down_revision = "f3a9c1d8e2b7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("two_fa_enabled", sa.Boolean(), nullable=False),
        sa.Column("two_fa_method", sa.String(length=20), nullable=True),
        sa.Column("two_fa_secret", sa.Text(), nullable=True),
        sa.Column("invited_by", sa.String(length=36), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('super_admin', 'admin', 'support')", name="ck_admin_users_role_valid"),
        sa.CheckConstraint("status IN ('active', 'invited', 'disabled')", name="ck_admin_users_status_valid"),
        sa.ForeignKeyConstraint(["invited_by"], ["admin_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_admin_users_email", "admin_users", ["email"])

    op.create_table(
        "admin_sessions",
        sa.Column("admin_user_id", sa.String(length=36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=255), nullable=True),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_sessions_admin_user_id", "admin_sessions", ["admin_user_id"])

    op.create_table(
        "audit_entries",
        sa.Column("actor_admin_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("target_label", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "target_type IN ('organization', 'user', 'event', 'admin', 'support_ticket')",
            name="ck_audit_entries_target_type_valid",
        ),
        sa.ForeignKeyConstraint(["actor_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_entries_actor_admin_id", "audit_entries", ["actor_admin_id"])
    op.create_index("ix_audit_entries_target", "audit_entries", ["target_type", "target_id"])
    op.create_index("ix_audit_entries_created_at", "audit_entries", ["created_at"])

    op.create_table(
        "platform_events",
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("recurrence_rule", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("audience", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('scheduled', 'ongoing', 'completed', 'cancelled')", name="ck_platform_events_status_valid"),
        sa.CheckConstraint("audience IN ('everyone', 'individuals', 'employees', 'selected_organizations')", name="ck_platform_events_audience_valid"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_platform_events_date_range"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_platform_events_title_not_empty"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "platform_event_organizations",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["platform_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "organization_id", name="uq_platform_event_organization"),
    )
    op.create_index("ix_platform_event_organizations_event_id", "platform_event_organizations", ["event_id"])
    op.create_index("ix_platform_event_organizations_organization_id", "platform_event_organizations", ["organization_id"])

    op.create_table(
        "platform_event_participants",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["platform_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "user_id", name="uq_platform_event_participant"),
    )
    op.create_index("ix_platform_event_participants_event_id", "platform_event_participants", ["event_id"])
    op.create_index("ix_platform_event_participants_user_id", "platform_event_participants", ["user_id"])

    op.add_column("organizations", sa.Column("deactivation_reason", sa.String(length=500), nullable=True))
    op.add_column("organization_subscriptions", sa.Column("platform_comped", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("organization_subscriptions", "platform_comped")
    op.drop_column("organizations", "deactivation_reason")

    op.drop_index("ix_platform_event_participants_user_id", table_name="platform_event_participants")
    op.drop_index("ix_platform_event_participants_event_id", table_name="platform_event_participants")
    op.drop_table("platform_event_participants")

    op.drop_index("ix_platform_event_organizations_organization_id", table_name="platform_event_organizations")
    op.drop_index("ix_platform_event_organizations_event_id", table_name="platform_event_organizations")
    op.drop_table("platform_event_organizations")

    op.drop_table("platform_events")

    op.drop_index("ix_audit_entries_created_at", table_name="audit_entries")
    op.drop_index("ix_audit_entries_target", table_name="audit_entries")
    op.drop_index("ix_audit_entries_actor_admin_id", table_name="audit_entries")
    op.drop_table("audit_entries")

    op.drop_index("ix_admin_sessions_admin_user_id", table_name="admin_sessions")
    op.drop_table("admin_sessions")

    op.drop_index("ix_admin_users_email", table_name="admin_users")
    op.drop_table("admin_users")
