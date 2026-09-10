"""add cooling_off status and client cooling_off_seconds

Revision ID: c9d0e1f2a3b4
Revises: e6f7a8b9c0d1
Create Date: 2026-09-10 10:30:00.000000

New COOLING_OFF order status sits between validated and processing: when a
client (store) has a cooling-off period configured, a validated order waits
in cooling_off for that many seconds before being pushed to QPMN. Like
pending/processing, cooling_off is internal-only and never reported to OMS
or VFS. clients.cooling_off_seconds stores the per-client period (0 = none).
orders.cooling_off_seconds snapshots the period actually applied to each
order when it enters cooling_off, so the platform frontend can render the
countdown.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # MySQL requires modifying the ENUM column to add a new value
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','cooling_off','processing',"
        "'printready','printed','produced','cancelled','failed','errored','shipped') "
        "NOT NULL DEFAULT 'pending'"
    )
    op.add_column(
        'clients',
        sa.Column('cooling_off_seconds', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'orders',
        sa.Column('cooling_off_seconds', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'cooling_off_seconds')
    op.drop_column('clients', 'cooling_off_seconds')
    # Orders still in 'cooling_off' should be pushed or cancelled before
    # running downgrade.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','processing','printready',"
        "'printed','produced','cancelled','failed','errored','shipped') "
        "NOT NULL DEFAULT 'pending'"
    )
