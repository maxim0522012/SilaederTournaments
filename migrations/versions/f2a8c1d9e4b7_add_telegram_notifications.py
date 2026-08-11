"""add telegram notifications

Revision ID: f2a8c1d9e4b7
Revises: 4983486d7fd4
Create Date: 2026-08-11 17:10:00

"""
from alembic import op
import sqlalchemy as sa


revision = "f2a8c1d9e4b7"
down_revision = "4983486d7fd4"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    with op.batch_alter_table("users") as batch_op:
        if "telegram_username" not in columns:
            batch_op.add_column(sa.Column("telegram_username", sa.Text(), nullable=True))
        if "telegram_chat_id" not in columns:
            batch_op.add_column(sa.Column("telegram_chat_id", sa.Text(), nullable=True))
        if "telegram_link_token" not in columns:
            batch_op.add_column(sa.Column("telegram_link_token", sa.Text(), nullable=True))

    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")}
    if "idx_users_telegram_username" not in indexes:
        op.create_index("idx_users_telegram_username", "users", ["telegram_username"], unique=True)
    if "idx_users_telegram_link_token" not in indexes:
        op.create_index("idx_users_telegram_link_token", "users", ["telegram_link_token"], unique=True)


def downgrade():
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")}
    if "idx_users_telegram_link_token" in indexes:
        op.drop_index("idx_users_telegram_link_token", table_name="users")
    if "idx_users_telegram_username" in indexes:
        op.drop_index("idx_users_telegram_username", table_name="users")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    with op.batch_alter_table("users") as batch_op:
        for column in ("telegram_link_token", "telegram_chat_id", "telegram_username"):
            if column in columns:
                batch_op.drop_column(column)
