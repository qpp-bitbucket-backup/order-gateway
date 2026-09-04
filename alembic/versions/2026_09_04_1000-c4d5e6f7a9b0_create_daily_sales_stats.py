"""create daily_sales_stats table

Revision ID: c4d5e6f7a9b0
Revises: b2c3d4e5f6a8
Create Date: 2026-09-04 10:00:00.000000

Daily per-client (store) sales statistics written by the Celery beat task
``tasks.stats.aggregate_daily_sales_stats``. One row per (client_id, stat_date);
metrics cover orders created that UTC day, with item/amount totals over the
orders successfully submitted to QPMN (store_order_id set).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a9b0'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'daily_sales_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('stat_date', sa.Date(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('store_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('currency', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=True),
        sa.Column('orders_requested', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('orders_submitted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('line_items_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('line_items_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_amount', sa.Float(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name='fk_daily_sales_stats_client_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_id', 'stat_date', name='uq_daily_sales_stats_client_date'),
    )
    op.create_index(op.f('ix_daily_sales_stats_client_id'), 'daily_sales_stats', ['client_id'], unique=False)
    op.create_index(op.f('ix_daily_sales_stats_store_id'), 'daily_sales_stats', ['store_id'], unique=False)
    op.create_index('ix_daily_sales_stats_stat_date', 'daily_sales_stats', ['stat_date'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_daily_sales_stats_stat_date', table_name='daily_sales_stats')
    op.drop_index(op.f('ix_daily_sales_stats_store_id'), table_name='daily_sales_stats')
    op.drop_index(op.f('ix_daily_sales_stats_client_id'), table_name='daily_sales_stats')
    op.drop_table('daily_sales_stats')
