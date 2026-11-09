"""add attribute_values to skus

Revision ID: d9e0f1a2b3c4
Revises: c7d8e9f0a1b2
Create Date: 2026-11-09 10:30:00.000000

Legacy QPMN create-order API sends ``attributeValues`` (``[{code, value}]``
attribute pairs) next to ``properties`` / ``customizeProject`` on each line
item. The pairs are product-configuration data, so they live on the SKU and
are persisted during product sync from the QPMN order sample data API
(items[].attributeValues). NULL = the SKU has no attribute pairs yet (older
SKUs until the next sync backfills them) — the legacy payload builder then
omits the field, matching the pre-change wire format.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'skus',
        sa.Column(
            'attribute_values',
            sa.JSON(),
            nullable=True,
            comment=(
                'Legacy API attributeValues: [{code, value}] attribute '
                'pairs from QPMN sample data, sent verbatim on legacy order '
                'push'
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('skus', 'attribute_values')
