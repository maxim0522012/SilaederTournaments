"""repair telegram schema on existing volumes

Revision ID: d9e6f3a1c8b4
Revises: c7d4e9a2b6f1
Create Date: 2026-08-11 19:05:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d9e6f3a1c8b4"
down_revision = "c7d4e9a2b6f1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "users" in tables:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
        for name in ("telegram_username", "telegram_chat_id", "telegram_link_token"):
            if name not in columns:
                op.add_column("users", sa.Column(name, sa.Text(), nullable=True))

        index_names = {
            row[0] for row in bind.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name IS NOT NULL")
            )
        }
        if "idx_users_telegram_username" not in index_names:
            op.create_index("idx_users_telegram_username", "users", ["telegram_username"], unique=True)
        if "idx_users_telegram_link_token" not in index_names:
            op.create_index("idx_users_telegram_link_token", "users", ["telegram_link_token"], unique=True)

    if "telegram_polling_state" not in tables:
        op.create_table(
            "telegram_polling_state",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("update_offset", sa.BigInteger(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute("INSERT OR IGNORE INTO telegram_polling_state(id,update_offset) VALUES(1,0)")
    op.execute("PRAGMA optimize")


def downgrade():
    # The repaired objects belong to the preceding Telegram migrations.
    pass
