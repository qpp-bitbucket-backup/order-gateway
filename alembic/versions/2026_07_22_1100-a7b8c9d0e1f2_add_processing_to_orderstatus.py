"""add processing to orderstatus enum

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-22

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL requires modifying the ENUM column to add a new value
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','processing','printready',"
        "'printed','cancelled','failed','errored','shipped') "
        "NOT NULL DEFAULT 'pending'"
    )


def downgrade() -> None:
    # Remove 'processing' from ENUM; orders with 'processing' status should be
    # migrated to another status before running downgrade.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','printready',"
        "'printed','cancelled','failed','errored','shipped') "
        "NOT NULL DEFAULT 'pending'"
    )
