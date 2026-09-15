"""create notification_email_logs and client notification_config

Revision ID: d5e6f7a8b9c0
Revises: c9d0e1f2a3b4
Create Date: 2026-09-14 10:30:00.000000

Level-based order status change email notifications:

- Every status transition is recorded in ``order.logs`` with a level tag
  (info / warning / error) via ``record_status_change``.
- ``clients.notification_config`` (JSON, NULL) stores each client's per-level
  settings: {"info"|"warning"|"error": {"enabled": bool, "emails": [...]}}.
  NULL = all levels disabled — existing clients keep today's behaviour.
- ``notification_email_logs`` rows track each outbound status-change email
  (one per transition), mirroring the outbound WebhookLog pattern: the
  ``notify_order_status_email`` task resolves the client's recipients for
  the transition's level and marks the row processed / skipped / failed,
  retrying sends with exponential backoff.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'clients',
        sa.Column('notification_config', sa.JSON(), nullable=True),
    )

    op.create_table(
        'notification_email_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('order_id', sa.String(length=64), nullable=True),
        sa.Column('source_order_id', sa.String(length=64), nullable=True),
        sa.Column('store_id', sa.String(length=128), nullable=True),
        sa.Column('from_status', sa.String(length=32), nullable=True),
        sa.Column('to_status', sa.String(length=32), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('recipients', sa.JSON(), nullable=True),
        sa.Column('process_status', sa.String(length=16), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notification_email_logs_order_id'), 'notification_email_logs', ['order_id'], unique=False)
    op.create_index(op.f('ix_notification_email_logs_store_id'), 'notification_email_logs', ['store_id'], unique=False)
    op.create_index(op.f('ix_notification_email_logs_to_status'), 'notification_email_logs', ['to_status'], unique=False)
    op.create_index(op.f('ix_notification_email_logs_level'), 'notification_email_logs', ['level'], unique=False)
    op.create_index(op.f('ix_notification_email_logs_process_status'), 'notification_email_logs', ['process_status'], unique=False)
    op.create_index('ix_notification_email_logs_order_id_created_at', 'notification_email_logs', ['order_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notification_email_logs_order_id_created_at', table_name='notification_email_logs')
    op.drop_index(op.f('ix_notification_email_logs_process_status'), table_name='notification_email_logs')
    op.drop_index(op.f('ix_notification_email_logs_level'), table_name='notification_email_logs')
    op.drop_index(op.f('ix_notification_email_logs_to_status'), table_name='notification_email_logs')
    op.drop_index(op.f('ix_notification_email_logs_store_id'), table_name='notification_email_logs')
    op.drop_index(op.f('ix_notification_email_logs_order_id'), table_name='notification_email_logs')
    op.drop_table('notification_email_logs')
    op.drop_column('clients', 'notification_config')
