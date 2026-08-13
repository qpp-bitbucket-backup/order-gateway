"""add_store_order_item_id_event_id_details_to_webhook_logs

Revision ID: d6e7f8a9b0c1
Revises: 07cb5a0806ef
Create Date: 2026-08-11 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = '07cb5a0806ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (expand step — purely additive, safe alongside old code)."""
    op.execute(
        "ALTER TABLE webhook_logs "
        "ADD COLUMN store_order_item_id VARCHAR(64) NULL AFTER store_order_id"
    )
    op.create_index(op.f('ix_webhook_logs_store_order_item_id'), 'webhook_logs', ['store_order_item_id'], unique=False)

    op.execute(
        "ALTER TABLE webhook_logs "
        "ADD COLUMN event_id VARCHAR(128) NULL AFTER store_order_item_id"
    )
    op.create_index(op.f('ix_webhook_logs_event_id'), 'webhook_logs', ['event_id'], unique=False)

    op.execute(
        "ALTER TABLE webhook_logs "
        "ADD COLUMN details TEXT NULL AFTER process_status"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('webhook_logs', 'details')
    op.drop_index(op.f('ix_webhook_logs_event_id'), table_name='webhook_logs')
    op.drop_column('webhook_logs', 'event_id')
    op.drop_index(op.f('ix_webhook_logs_store_order_item_id'), table_name='webhook_logs')
    op.drop_column('webhook_logs', 'store_order_item_id')
