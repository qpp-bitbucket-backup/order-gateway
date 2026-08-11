"""rename_move_store_item_id_on_webhook_logs

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-11 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename store_item_id -> store_order_item_id and physically move it
    next to store_order_id (it was appended at the end of the table by the
    migration that originally added it)."""
    op.execute(
        "ALTER TABLE webhook_logs "
        "CHANGE COLUMN store_item_id store_order_item_id VARCHAR(64) NULL "
        "AFTER store_order_id"
    )
    op.execute(
        "ALTER TABLE webhook_logs "
        "RENAME INDEX ix_webhook_logs_store_item_id TO ix_webhook_logs_store_order_item_id"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE webhook_logs "
        "RENAME INDEX ix_webhook_logs_store_order_item_id TO ix_webhook_logs_store_item_id"
    )
    op.execute(
        "ALTER TABLE webhook_logs "
        "CHANGE COLUMN store_order_item_id store_item_id VARCHAR(64) NULL"
    )
