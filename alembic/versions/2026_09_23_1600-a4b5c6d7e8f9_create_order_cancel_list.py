"""create order_cancel_list

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-23 16:00:00.000000

Deferred OMS order cancellations:

- The OMS cancel endpoint (POST /api/oms/order/{source_order_id}/cancel)
  returns 404 when the sourceOrderId matches no active order — e.g. the
  customer cancelled in OMS before VFS's order-create request reached the
  gateway. Those requests are now remembered in ``order_cancel_list`` as
  ``pending`` rows (idempotent per sourceOrderId).
- When the VFS-created order finally enters the order_publishing worker,
  a pending record cancels it locally right away (status -> cancelled,
  status-change email as usual) instead of publishing it — the order has
  not been pushed to QPMN at that point, so no QPMN cancel API call is
  needed. The record is stamped ``processed`` with the internal order_id.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'order_cancel_list',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('source_order_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.String(length=512), nullable=True),
        sa.Column('order_id', sa.String(length=64), nullable=True),
        sa.Column('processed_message', sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_order_cancel_list_source_order_id'), 'order_cancel_list', ['source_order_id'], unique=False)
    op.create_index(op.f('ix_order_cancel_list_status'), 'order_cancel_list', ['status'], unique=False)
    op.create_index(op.f('ix_order_cancel_list_order_id'), 'order_cancel_list', ['order_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_order_cancel_list_order_id'), table_name='order_cancel_list')
    op.drop_index(op.f('ix_order_cancel_list_status'), table_name='order_cancel_list')
    op.drop_index(op.f('ix_order_cancel_list_source_order_id'), table_name='order_cancel_list')
    op.drop_table('order_cancel_list')
