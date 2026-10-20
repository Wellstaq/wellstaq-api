"""Fix evidence-submission review timestamps and notification types.

Revision ID: d5e7f9a1b3c4
Revises: c9d4e7a2f6b1
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e7f9a1b3c4"
down_revision = "c9d4e7a2f6b1"
branch_labels = None
depends_on = None


_OLD_NOTIFICATION_TYPES = (
    "liked_post",
    "commented_post",
    "new_message",
    "joined_club",
    "joined_department",
    "event_reminder",
    "challenge_completed",
    "challenge_daily_goal_met",
    "announcement",
)

_NEW_NOTIFICATION_TYPES = (
    *_OLD_NOTIFICATION_TYPES[:-1],
    "challenge_submission_approved",
    "challenge_submission_rejected",
    _OLD_NOTIFICATION_TYPES[-1],
)


def _notification_type_constraint(values: tuple[str, ...]) -> str:
    return f"notification_type IN ({', '.join(repr(value) for value in values)})"


def upgrade():
    # reviewed_at stores an aware UTC value in the service.  The original
    # migration created a timestamp without time zone, which asyncpg rejects
    # when an aware datetime is bound.
    op.alter_column(
        "challenge_progress_entries",
        "reviewed_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="reviewed_at AT TIME ZONE 'UTC'",
    )

    # The review service emits these two notification types, so both the
    # Python allow-list and the database constraint must accept them.
    op.drop_constraint("ck_notification_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notification_type",
        "notifications",
        _notification_type_constraint(_NEW_NOTIFICATION_TYPES),
    )


def downgrade():
    op.drop_constraint("ck_notification_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notification_type",
        "notifications",
        _notification_type_constraint(_OLD_NOTIFICATION_TYPES),
    )

    op.alter_column(
        "challenge_progress_entries",
        "reviewed_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
        postgresql_using="reviewed_at AT TIME ZONE 'UTC'",
    )
