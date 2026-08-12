"""add match challenges

Revision ID: e4b7c2d8a5f0
Revises: d9e6f3a1c8b4
Create Date: 2026-08-12 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "e4b7c2d8a5f0"
down_revision = "d9e6f3a1c8b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_challenges",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("challenger", sa.Text(), nullable=False),
        sa.Column("opponent", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["challenger"], ["users.id"]),
        sa.ForeignKeyConstraint(["opponent"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_match_challenges_opponent_status", "match_challenges", ["opponent", "status"])
    op.create_index("idx_match_challenges_challenger_status", "match_challenges", ["challenger", "status"])


def downgrade():
    op.drop_index("idx_match_challenges_challenger_status", table_name="match_challenges")
    op.drop_index("idx_match_challenges_opponent_status", table_name="match_challenges")
    op.drop_table("match_challenges")
