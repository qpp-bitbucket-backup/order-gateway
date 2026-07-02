"""Add store_id to clients table

Revision ID: b7e2f4a91c38
Revises: a3c8d91e4f20
Create Date: 2026-06-18 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "b7e2f4a91c38"
down_revision: Union[str, Sequence[str], None] = "a3c8d91e4f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = inspect(bind)
    if "clients" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("clients")}
    if "store_id" in columns:
        return

    op.add_column(
        "clients",
        sa.Column("store_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.execute("UPDATE clients SET store_id = CONCAT('store_', id) WHERE store_id IS NULL")
    op.alter_column("clients", "store_id", existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=False)
    op.create_index(op.f("ix_clients_store_id"), "clients", ["store_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_clients_store_id"), table_name="clients")
    op.drop_column("clients", "store_id")
