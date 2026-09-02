"""add produced status and item-level tracking to orders

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e6f7
Create Date: 2026-08-27 18:00:00.000000

TI-65: adds 'produced' to the orders.status ENUM (order-level status reached
once every QPMN order item has been physically printed), plus two JSON
columns used to track per-item production progress:
  - store_order_item_ids: the full set of QPMN-assigned item ids expected
    for this order, captured from QPMN's create-order response at push time.
  - produced_item_ids: item ids that have reported "produced" so far via
    inbound order_item_produced webhooks.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # MySQL requires modifying the ENUM column to add a new value
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','processing','printready',"
        "'printed','produced','cancelled','failed','errored','shipped') "
        "NOT NULL DEFAULT 'pending'"
    )
    op.add_column('orders', sa.Column('store_order_item_ids', sa.JSON(), nullable=True))
    op.add_column('orders', sa.Column('produced_item_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'produced_item_ids')
    op.drop_column('orders', 'store_order_item_ids')
    # Orders with 'produced' status should be migrated to another status
    # before running downgrade.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','processing','printready',"
        "'printed','cancelled','failed','errored','shipped') "
        "NOT NULL DEFAULT 'pending'"
    )
