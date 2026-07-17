"""create_webhook_logs_table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'webhook_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('order_id', sa.String(length=64), nullable=True),
        sa.Column('source_order_id', sa.String(length=64), nullable=True),
        sa.Column('event_status', sa.String(length=32), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('signature_valid', sa.Boolean(), nullable=True),
        sa.Column('process_status', sa.String(length=16), nullable=False),
        sa.Column('error_message', sa.String(length=512), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhook_logs_direction'), 'webhook_logs', ['direction'], unique=False)
    op.create_index(op.f('ix_webhook_logs_source'), 'webhook_logs', ['source'], unique=False)
    op.create_index(op.f('ix_webhook_logs_order_id'), 'webhook_logs', ['order_id'], unique=False)
    op.create_index(op.f('ix_webhook_logs_source_order_id'), 'webhook_logs', ['source_order_id'], unique=False)
    op.create_index(op.f('ix_webhook_logs_event_status'), 'webhook_logs', ['event_status'], unique=False)
    op.create_index(op.f('ix_webhook_logs_process_status'), 'webhook_logs', ['process_status'], unique=False)
    op.create_index('ix_webhook_logs_order_id_created_at', 'webhook_logs', ['order_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_webhook_logs_order_id_created_at', table_name='webhook_logs')
    op.drop_index(op.f('ix_webhook_logs_process_status'), table_name='webhook_logs')
    op.drop_index(op.f('ix_webhook_logs_event_status'), table_name='webhook_logs')
    op.drop_index(op.f('ix_webhook_logs_source_order_id'), table_name='webhook_logs')
    op.drop_index(op.f('ix_webhook_logs_order_id'), table_name='webhook_logs')
    op.drop_index(op.f('ix_webhook_logs_source'), table_name='webhook_logs')
    op.drop_index(op.f('ix_webhook_logs_direction'), table_name='webhook_logs')
    op.drop_table('webhook_logs')
