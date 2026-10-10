"""Evidence-based challenge tracking: tracking_method, activity definition,
evidence/review fields on progress entries, participant scores, and
completed/upcoming counts on the stats snapshot.

Revision ID: f3a9c1d8e2b7
Revises: e4b8197d4a21
"""

from alembic import op
import sqlalchemy as sa

revision = "f3a9c1d8e2b7"
down_revision = "e4b8197d4a21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("challenges", sa.Column("tracking_method", sa.String(length=20), nullable=False, server_default="self_reported"))
    op.add_column("challenges", sa.Column("activity_label", sa.Text(), nullable=True))
    op.add_column("challenges", sa.Column("evidence_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("challenges", sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_check_constraint(
        "ck_challenges_tracking_method_valid", "challenges",
        "tracking_method IN ('automatic', 'self_reported', 'evidence_based', 'platform_recorded')",
    )

    op.add_column("challenge_participants", sa.Column("score", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_challenge_participants_score_range", "challenge_participants",
        "score IS NULL OR (score >= 0 AND score <= 100)",
    )

    op.add_column("challenge_progress_entries", sa.Column("photo_url", sa.String(length=2048), nullable=True))
    op.add_column("challenge_progress_entries", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("challenge_progress_entries", sa.Column("review_status", sa.String(length=20), nullable=False, server_default="approved"))
    op.add_column("challenge_progress_entries", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("challenge_progress_entries", sa.Column("reviewed_by", sa.String(length=36), nullable=True))
    op.add_column("challenge_progress_entries", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_challenge_progress_entries_reviewed_by", "challenge_progress_entries", "users",
        ["reviewed_by"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_challenge_progress_review_status_valid", "challenge_progress_entries",
        "review_status IN ('pending', 'approved', 'rejected')",
    )

    op.add_column("challenge_stats_snapshots", sa.Column("completed_challenges", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("challenge_stats_snapshots", sa.Column("upcoming_challenges", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("challenge_stats_snapshots", "upcoming_challenges")
    op.drop_column("challenge_stats_snapshots", "completed_challenges")

    op.drop_constraint("ck_challenge_progress_review_status_valid", "challenge_progress_entries", type_="check")
    op.drop_constraint("fk_challenge_progress_entries_reviewed_by", "challenge_progress_entries", type_="foreignkey")
    op.drop_column("challenge_progress_entries", "reviewed_at")
    op.drop_column("challenge_progress_entries", "reviewed_by")
    op.drop_column("challenge_progress_entries", "rejection_reason")
    op.drop_column("challenge_progress_entries", "review_status")
    op.drop_column("challenge_progress_entries", "note")
    op.drop_column("challenge_progress_entries", "photo_url")

    op.drop_constraint("ck_challenge_participants_score_range", "challenge_participants", type_="check")
    op.drop_column("challenge_participants", "score")

    op.drop_constraint("ck_challenges_tracking_method_valid", "challenges", type_="check")
    op.drop_column("challenges", "requires_approval")
    op.drop_column("challenges", "evidence_required")
    op.drop_column("challenges", "activity_label")
    op.drop_column("challenges", "tracking_method")
