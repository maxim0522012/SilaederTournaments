"""remove legacy direct email and telegram fields

Revision ID: f5c8d3e9b1a2
Revises: e4b7c2d8a5f0
Create Date: 2026-08-12 16:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "f5c8d3e9b1a2"
down_revision = "e4b7c2d8a5f0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "telegram_polling_state" in tables:
        op.drop_table("telegram_polling_state")
    if "users" in tables:
        columns = {column["name"] for column in inspector.get_columns("users")}
        indexes = {index["name"] for index in inspector.get_indexes("users")}
        with op.batch_alter_table("users") as batch:
            if "idx_users_telegram_link_token" in indexes:
                batch.drop_index("idx_users_telegram_link_token")
            if "idx_users_telegram_username" in indexes:
                batch.drop_index("idx_users_telegram_username")
            for name in ("telegram_link_token", "telegram_chat_id", "telegram_username"):
                if name in columns:
                    batch.drop_column(name)


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("telegram_username", sa.Text(), nullable=True))
        batch.add_column(sa.Column("telegram_chat_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("telegram_link_token", sa.Text(), nullable=True))
        batch.create_index("idx_users_telegram_username", ["telegram_username"], unique=True)
        batch.create_index("idx_users_telegram_link_token", ["telegram_link_token"], unique=True)
    op.create_table(
        "telegram_polling_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("update_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO telegram_polling_state(id,update_offset) VALUES(1,0)")
