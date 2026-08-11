"""add_store_item_id_to_webhook_logs

Revision ID: c9d0e1f2a3b4
Revises: a7b8c9d0e1f2
Create Date: 2026-08-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('webhook_logs', sa.Column('store_item_id', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_webhook_logs_store_item_id'), 'webhook_logs', ['store_item_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_webhook_logs_store_item_id'), table_name='webhook_logs')
    op.drop_column('webhook_logs', 'store_item_id')
