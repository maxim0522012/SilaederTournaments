"""add sport separation and chess

Revision ID: a6d9e4f2c3b1
Revises: f5c8d3e9b1a2
Create Date: 2026-08-12 18:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "a6d9e4f2c3b1"
down_revision = "f5c8d3e9b1a2"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("matches", "requests", "match_challenges", "tournaments"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("sport", sa.Text(), nullable=False, server_default="tennis"))
    op.create_index("idx_matches_sport_created", "matches", ["sport", "created_at"])
    op.create_index("idx_tournaments_sport_start", "tournaments", ["sport", "start_at"])
    op.execute("PRAGMA optimize")


def downgrade():
    op.drop_index("idx_tournaments_sport_start", table_name="tournaments")
    op.drop_index("idx_matches_sport_created", table_name="matches")
    for table in ("tournaments", "match_challenges", "requests", "matches"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("sport")
