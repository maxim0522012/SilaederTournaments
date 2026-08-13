"""add lichess account links

Revision ID: b7e0f5a3d4c2
Revises: a6d9e4f2c3b1
Create Date: 2026-08-13 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "b7e0f5a3d4c2"
down_revision = "a6d9e4f2c3b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lichess_accounts",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("lichess_id", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("rapid_rating", sa.Integer()),
        sa.Column("blitz_rating", sa.Integer()),
        sa.Column("classical_rating", sa.Integer()),
        sa.Column("connected_at", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("idx_lichess_accounts_lichess_id", "lichess_accounts", ["lichess_id"], unique=True)
    op.execute("PRAGMA optimize")


def downgrade():
    op.drop_index("idx_lichess_accounts_lichess_id", table_name="lichess_accounts")
    op.drop_table("lichess_accounts")
