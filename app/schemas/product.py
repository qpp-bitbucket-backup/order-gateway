from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


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
    createdAt: Optional[datetime] = Field(None, description="Creation timestamp")
    updatedAt: Optional[datetime] = Field(None, description="Last update timestamp")

    model_config = ConfigDict(populate_by_name=True)


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
    sourceSku: Optional[str] = Field(None, max_length=255, description="Third-party platform SKU: regex pattern (re.fullmatch) or literal")
    description: Optional[str] = Field(None, description="SKU description")
    productId: str = Field(..., description="Associated product ID")
    active: bool = Field(True, description="Whether SKU is active")
    unitPrice: Optional[float] = Field(None, ge=0, description="Unit price")
    unitCost: Optional[float] = Field(None, ge=0, description="Unit cost")
    maxPackageQuantity: int = Field(50, ge=1, description="Max quantity per package")
    properties: Optional[Dict[str, Any]] = Field(None, description="SKU properties")
    customizeProject: Optional[Dict[str, Any]] = Field(None, description="Customize project data")
    productDesignData: Optional[Dict[str, Any]] = Field(None, description="Open API productDesignData (used when QPMN_ORDER_API_VERSION=open)")
    createdAt: Optional[datetime] = Field(None, description="Creation timestamp")
    updatedAt: Optional[datetime] = Field(None, description="Last update timestamp")

    model_config = ConfigDict(populate_by_name=True)


class SkusListResponse(BaseModel):
    """Schema for SKUs list response."""
    success: bool = Field(..., description="Request success status")
    count: int = Field(..., description="Total number of SKUs")
    page: int = Field(1, description="Current page number")
    pages: int = Field(1, description="Total number of pages")
    data: List[Sku] = Field(..., description="List of SKUs")


class SkuUpdateRequest(BaseModel):
    """Schema for SKU update request. Only provided fields will be updated."""
    code: Optional[str] = Field(None, description="SKU code")
    sourceSku: Optional[str] = Field(None, max_length=255, description="Third-party platform SKU: regex pattern (re.fullmatch) or literal")
    description: Optional[str] = Field(None, description="SKU description")
    productId: Optional[str] = Field(None, description="Associated product ID")
    active: Optional[bool] = Field(None, description="Whether SKU is active")
    unitPrice: Optional[float] = Field(None, ge=0, description="Unit price")
    unitCost: Optional[float] = Field(None, ge=0, description="Unit cost")
    maxPackageQuantity: Optional[int] = Field(None, ge=1, description="Max quantity per package")
    properties: Optional[Dict[str, Any]] = Field(None, description="SKU properties")
    customizeProject: Optional[Dict[str, Any]] = Field(None, description="Customize project data")
    productDesignData: Optional[Dict[str, Any]] = Field(None, description="Open API productDesignData (used when QPMN_ORDER_API_VERSION=open)")


class SkuUpdateResponse(BaseModel):
    """Schema for SKU update response."""
    success: bool = Field(True, description="Update success status")
    message: str = Field("SKU updated successfully", description="Update message")
    sku: Optional[Sku] = Field(None, description="Updated SKU details")
