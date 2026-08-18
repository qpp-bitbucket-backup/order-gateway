"""set order version default to 1

Revision ID: f6a7b8c9d0e1
Revises: f1a2b3c4d5e6
Create Date: 2026-07-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update existing rows with version=0 to version=1
    op.execute("UPDATE orders SET version = 1 WHERE version = 0")
    # Change default value from 0 to 1
    op.alter_column(
        'orders',
        'version',
        server_default='1',
    )


def downgrade() -> None:
    op.alter_column(
        'orders',
        'version',
        server_default='0',
    )
