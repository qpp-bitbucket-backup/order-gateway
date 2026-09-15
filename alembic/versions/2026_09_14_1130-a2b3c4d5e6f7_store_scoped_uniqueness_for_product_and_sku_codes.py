"""store-scoped uniqueness for product_code and sku code

Revision ID: a2b3c4d5e6f7
Revises: d5e6f7a8b9c0
Create Date: 2026-09-14 11:30:00.000000

product_code / skus.code hold the QPMN product/SKU *name*. Identical names
across different stores (clients) are legitimate, but the original global
unique indexes (ix_products_product_code, ix_skus_code) rejected them with
MySQL 1062 during product sync. Replace them with regular indexes and add
store-scoped unique constraints instead:

- products: UNIQUE (store_id, product_code)
- skus:     UNIQUE (store_id, code)

product_id / sku_id (QPMN _id) remain globally unique. Rows with NULL
store_id (legacy) are exempt from the new constraints (MySQL unique indexes
ignore NULLs).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # products: global unique -> per-store unique
    op.drop_index("ix_products_product_code", table_name="products")
    op.create_index(
        "ix_products_product_code", "products", ["product_code"], unique=False
    )
    op.create_unique_constraint(
        "uq_products_store_product_code", "products", ["store_id", "product_code"]
    )

    # skus: global unique -> per-store unique
    op.drop_index("ix_skus_code", table_name="skus")
    op.create_index("ix_skus_code", "skus", ["code"], unique=False)
    op.create_unique_constraint("uq_skus_store_code", "skus", ["store_id", "code"])


def downgrade() -> None:
    op.drop_constraint("uq_skus_store_code", table_name="skus", type_="unique")
    op.drop_index("ix_skus_code", table_name="skus")
    op.create_index("ix_skus_code", "skus", ["code"], unique=True)

    op.drop_constraint("uq_products_store_product_code", table_name="products", type_="unique")
    op.drop_index("ix_products_product_code", table_name="products")
    op.create_index(
        "ix_products_product_code", "products", ["product_code"], unique=True
    )
