from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class ProductComponent(BaseModel):
    """Product component schema."""
    code: Optional[str] = Field(None, description="Component code")
    type: Optional[str] = Field(None, description="Component type")
    required: Optional[bool] = Field(False, description="Whether component is required")
    attributes: Optional[Dict[str, Any]] = Field(None, description="Component attributes")


class Product(BaseModel):
    """Product schema for available print products."""
    id: str = Field(..., alias="_id", description="Internal product ID")
    productCode: str = Field(..., description="Product code")
    description: Optional[str] = Field(None, description="Product description")
    components: Optional[List[Dict[str, Any]]] = Field(None, description="Product components")

    class Config:
        populate_by_name = True


class ProductsListResponse(BaseModel):
    """Schema for products list response."""
    success: bool = Field(..., description="Request success status")
    count: int = Field(..., description="Total number of products")
    page: int = Field(1, description="Current page number")
    pages: int = Field(1, description="Total number of pages")
    data: List[Product] = Field(..., description="List of products")


class Sku(BaseModel):
    """SKU schema."""
    id: str = Field(..., alias="_id", description="Internal SKU ID")
    code: str = Field(..., description="SKU code")
    description: Optional[str] = Field(None, description="SKU description")
    productId: str = Field(..., description="Associated product ID")
    active: bool = Field(True, description="Whether SKU is active")
    unitPrice: Optional[float] = Field(None, ge=0, description="Unit price")
    unitCost: Optional[float] = Field(None, ge=0, description="Unit cost")

    class Config:
        populate_by_name = True


class SkusListResponse(BaseModel):
    """Schema for SKUs list response."""
    success: bool = Field(..., description="Request success status")
    count: int = Field(..., description="Total number of SKUs")
    page: int = Field(1, description="Current page number")
    pages: int = Field(1, description="Total number of pages")
    data: List[Sku] = Field(..., description="List of SKUs")
