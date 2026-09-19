"""Post galleries and story likes.

Revision ID: b7a42d01e69c
Revises: a12f7c3e91b4
"""
from alembic import op
import sqlalchemy as sa

revision = "b7a42d01e69c"
down_revision = "a12f7c3e91b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("media_urls", sa.JSON(), nullable=True))
    posts = sa.table("posts", sa.column("id", sa.String()), sa.column("media_url", sa.String()), sa.column("media_urls", sa.JSON()))
    connection = op.get_bind()
    for post_id, media_url in connection.execute(sa.select(posts.c.id, posts.c.media_url)):
        if media_url:
            connection.execute(posts.update().where(posts.c.id == post_id).values(media_urls=[media_url]))

    op.add_column("stories", sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "story_likes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("story_id", sa.String(36), sa.ForeignKey("stories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("story_id", "user_id", name="uq_story_likes_story_user"),
    )
    op.create_index("ix_story_likes_organization_id", "story_likes", ["organization_id"])
    op.create_index("ix_story_likes_user_id", "story_likes", ["user_id"])


def downgrade():
    op.drop_index("ix_story_likes_user_id", table_name="story_likes")
    op.drop_index("ix_story_likes_organization_id", table_name="story_likes")
    op.drop_table("story_likes")
    op.drop_column("stories", "like_count")
    op.drop_column("posts", "media_urls")
