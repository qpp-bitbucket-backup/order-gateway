from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, UniqueConstraint
from typing import Optional, List, Dict, Any
from app.models.base import BaseModel


class ProductComponent(SQLModel):
    """Product component definition."""
    code: Optional[str] = Field(None, description="Component code")
    type: Optional[str] = Field(None, description="Component type")
    required: Optional[bool] = Field(False, description="Whether component is required")
    attributes: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Component attributes")


class Product(BaseModel, table=True):
    """Product model representing available print products."""

    __tablename__ = "products"
    # product_code is the QPMN product *name* — names only have to be unique
    # within a store (client); different stores may sell identically named
    # products, so uniqueness is scoped to (store_id, product_code) instead
    # of the old global unique index. NULL store_id rows (legacy data) are
    # exempt from the constraint in MySQL (NULL != NULL in unique indexes).
    __table_args__ = (
        UniqueConstraint("store_id", "product_code", name="uq_products_store_product_code"),
    )

    product_id: str = Field(unique=True, index=True, nullable=False, description="Internal product ID (_id)")
    product_code: str = Field(index=True, nullable=False, description="Product code (QPMN product name; unique per store only)")
    description: Optional[str] = Field(None, description="Product description")
    components: Optional[List[Dict[str, Any]]] = Field(None, sa_column=Column(JSON), description="Product components")
    is_active: bool = Field(default=True, nullable=False, description="Whether product is active")
    store_id: Optional[str] = Field(None, index=True, description="Store identifier")


class Sku(BaseModel, table=True):
    """SKU model representing stock keeping units."""

    __tablename__ = "skus"
    # code is the QPMN SKU *name* — same store-scoped uniqueness as
    # products.product_code: identical SKU names across different stores are
    # legitimate and were wrongly rejected by the old global unique index.
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uq_skus_store_code"),
    )

    sku_id: str = Field(unique=True, index=True, nullable=False, description="Internal SKU ID (_id)")
    code: str = Field(index=True, nullable=False, description="SKU code (QPMN SKU name; unique per store only)")
    source_sku: Optional[str] = Field(None, max_length=255, index=True, description="Third-party platform SKU: a regex pattern (re.fullmatch) or literal matched against items[].sku during order validation")
    description: Optional[str] = Field(None, description="SKU description")
    product_id: str = Field(nullable=False, index=True, description="Associated product ID")
    active: bool = Field(default=True, nullable=False, description="Whether SKU is active")
    unit_price: Optional[float] = Field(None, ge=0, description="Unit price")
    unit_cost: Optional[float] = Field(None, ge=0, description="Unit cost")
    max_package_quantity: int = Field(default=50, ge=1, description="Max quantity per package")
    store_id: Optional[str] = Field(None, index=True, description="Store identifier")
    properties: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="SKU properties")
    customize_project: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Customize project data")
    product_design_data: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Open API productDesignData (used when QPMN_ORDER_API_VERSION=open)")
