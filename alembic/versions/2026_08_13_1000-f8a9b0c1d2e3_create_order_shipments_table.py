"""create_order_shipments_table

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-08-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'order_shipments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('order_id', sa.String(length=255), nullable=False),
        sa.Column('store_order_id', sa.String(length=64), nullable=True),
        sa.Column('qpmn_shipment_id', sa.String(length=64), nullable=True),
        sa.Column('shipment_index', sa.Integer(), nullable=True),
        sa.Column('tracking_number', sa.String(length=128), nullable=True),
        sa.Column('tracking_url', sa.String(length=512), nullable=True),
        sa.Column('carrier', sa.String(length=64), nullable=True),
        sa.Column('ship_date', sa.DateTime(), nullable=True),
        sa.Column('items', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['orders.order_id']),
    )
    op.create_index(op.f('ix_order_shipments_order_id'), 'order_shipments', ['order_id'], unique=False)
    op.create_index(op.f('ix_order_shipments_store_order_id'), 'order_shipments', ['store_order_id'], unique=False)
    op.create_index(op.f('ix_order_shipments_qpmn_shipment_id'), 'order_shipments', ['qpmn_shipment_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_order_shipments_qpmn_shipment_id'), table_name='order_shipments')
    op.drop_index(op.f('ix_order_shipments_store_order_id'), table_name='order_shipments')
    op.drop_index(op.f('ix_order_shipments_order_id'), table_name='order_shipments')
    op.drop_table('order_shipments')
