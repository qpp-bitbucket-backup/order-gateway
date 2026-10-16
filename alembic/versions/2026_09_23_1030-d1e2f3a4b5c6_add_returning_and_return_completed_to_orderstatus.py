"""add returning and returncompleted to orderstatus enum

Revision ID: d1e2f3a4b5c6
Revises: b3c4d5e6f7a8
Create Date: 2026-09-23 10:30:00.000000

Two new order statuses for the OMS return flow: SHIPPED orders may
transition to RETURNING (退貨, via the OMS return API after QPMN accepts
the return), which then flows to the terminal RETURN_COMPLETED (退貨完成,
completion signal to be wired up once QPMN defines its feedback event).

Status values follow the no-underscore spelling used by ``printready``
(e.g. ``returncompleted``).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # MySQL requires modifying the ENUM column to add new values
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','cooling_off','processing',"
        "'printready','printed','produced','cancelled','failed','errored',"
        "'shipped','returning','returncompleted') "
        "NOT NULL DEFAULT 'pending'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Orders in 'returning'/'returncompleted' should be resolved before
    # running downgrade.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','cooling_off','processing',"
        "'printready','printed','produced','cancelled','failed','errored',"
        "'shipped') "
        "NOT NULL DEFAULT 'pending'"
    )
