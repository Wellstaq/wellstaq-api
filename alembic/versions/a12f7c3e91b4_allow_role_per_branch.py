"""allow one role per branch while permitting multi-branch managers

Revision ID: a12f7c3e91b4
Revises: 6c13d8e2a4f1
"""

from alembic import op

revision = "a12f7c3e91b4"
down_revision = "6c13d8e2a4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_user_one_branch_role_per_organization", table_name="user_role_assignments")
    op.create_index(
        "uq_user_one_role_per_branch",
        "user_role_assignments",
        ["user_id", "organization_id", "branch_id"],
        unique=True,
        postgresql_where="branch_id IS NOT NULL",
    )
    # Backfill the mandatory dashboard-entry permission for tenant org roles.
    op.execute("""
        INSERT INTO role_permissions (id, role_id, permission_id, scope, created_at, updated_at)
        SELECT gen_random_uuid(), r.id, p.id, 'org', NOW(), NOW()
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.organization_id IS NOT NULL
          AND r.branch_id IS NULL
          AND r.name <> 'member'
          AND p.name = 'overview'
          AND NOT EXISTS (
              SELECT 1 FROM role_permissions rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
    """)


def downgrade() -> None:
    op.drop_index("uq_user_one_role_per_branch", table_name="user_role_assignments")
    op.create_index(
        "uq_user_one_branch_role_per_organization",
        "user_role_assignments",
        ["user_id", "organization_id"],
        unique=True,
        postgresql_where="branch_id IS NOT NULL",
    )
