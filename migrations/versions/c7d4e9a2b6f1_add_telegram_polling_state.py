"""add telegram polling state

Revision ID: c7d4e9a2b6f1
Revises: f2a8c1d9e4b7
Create Date: 2026-08-11 18:10:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c7d4e9a2b6f1"
down_revision = "f2a8c1d9e4b7"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "telegram_polling_state" not in tables:
        op.create_table(
            "telegram_polling_state",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("update_offset", sa.BigInteger(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute(
        "INSERT OR IGNORE INTO telegram_polling_state(id,update_offset) VALUES(1,0)"
    )


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "telegram_polling_state" in tables:
        op.drop_table("telegram_polling_state")
