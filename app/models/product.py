from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON
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

    product_id: str = Field(unique=True, index=True, nullable=False, description="Internal product ID (_id)")
    product_code: str = Field(unique=True, index=True, nullable=False, description="Product code")
    description: Optional[str] = Field(None, description="Product description")
    components: Optional[List[Dict[str, Any]]] = Field(None, sa_column=Column(JSON), description="Product components")
    is_active: bool = Field(default=True, nullable=False, description="Whether product is active")
    store_id: Optional[str] = Field(None, index=True, description="Store identifier")


class Sku(BaseModel, table=True):
    """SKU model representing stock keeping units."""

    __tablename__ = "skus"

    sku_id: str = Field(unique=True, index=True, nullable=False, description="Internal SKU ID (_id)")
    code: str = Field(unique=True, index=True, nullable=False, description="SKU code")
    description: Optional[str] = Field(None, description="SKU description")
    product_id: str = Field(nullable=False, index=True, description="Associated product ID")
    active: bool = Field(default=True, nullable=False, description="Whether SKU is active")
    unit_price: Optional[float] = Field(None, ge=0, description="Unit price")
    unit_cost: Optional[float] = Field(None, ge=0, description="Unit cost")
    store_id: Optional[str] = Field(None, index=True, description="Store identifier")
