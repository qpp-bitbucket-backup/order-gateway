"""add_event_id_to_webhook_logs

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-08-11 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add event_id, positioned right after store_order_item_id."""
    op.execute(
        "ALTER TABLE webhook_logs "
        "ADD COLUMN event_id VARCHAR(128) NULL AFTER store_order_item_id"
    )
    op.execute(
        "CREATE INDEX ix_webhook_logs_event_id ON webhook_logs (event_id)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX ix_webhook_logs_event_id ON webhook_logs")
    op.execute("ALTER TABLE webhook_logs DROP COLUMN event_id")
