from sqlmodel import SQLModel, Field, Relationship, Column
from sqlalchemy import JSON, Text
from typing import Optional, List, Dict, Any
from datetime import datetime,timezone
from enum import Enum
from app.models.base import BaseModel


class OrderStatus(str, Enum):
    """Order status enumeration."""
    RECEIVED = "received"
    PENDING = "pending"
    VALIDATED = "validated"
    PRINTREADY = "printready"
    PRINTED = "printed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    ERRORED = "errored"
    SHIPPED = "shipped"


# Order state machine - defines valid status transitions
ORDER_STATE_TRANSITIONS: Dict[OrderStatus, List[OrderStatus]] = {
    # 初始狀態：訂單剛接收
    OrderStatus.RECEIVED: [
        OrderStatus.PENDING,      # 進入待處理隊列
        OrderStatus.CANCELLED,    # 可直接取消
        OrderStatus.FAILED,       # 接收失敗
        OrderStatus.ERRORED       # 接收過程出錯
    ],
    # 待處理：進行驗證
    OrderStatus.PENDING: [
        OrderStatus.VALIDATED,    # 驗證通過
        OrderStatus.CANCELLED,    # 取消訂單
        OrderStatus.FAILED,       # 驗證失敗
        OrderStatus.ERRORED       # 驗證過程異常
    ],
    # 驗證通過：準備列印
    OrderStatus.VALIDATED: [
        OrderStatus.PRINTREADY,   # 進入列印隊列
        OrderStatus.CANCELLED,    # 取消訂單
        OrderStatus.FAILED,       # 準備列印失敗
        OrderStatus.ERRORED       # 準備過程異常
    ],
    # 列印就緒：開始列印， 已經Push 到QPMN 不能進行取消
    OrderStatus.PRINTREADY: [
        OrderStatus.PRINTED,      # 列印完成
        OrderStatus.FAILED,       # 列印失敗
        OrderStatus.ERRORED       # 列印過程異常
    ],
    # 已列印：準備出貨， 不能進行取消
    OrderStatus.PRINTED: [
        OrderStatus.SHIPPED,      # 已出貨（終端狀態）
        OrderStatus.FAILED,       # 出貨準備失敗
        OrderStatus.ERRORED       # 出貨過程異常
    ],
    # 失敗狀態：可重試
    OrderStatus.FAILED: [
        OrderStatus.PENDING,      # 重新回到待處理
        OrderStatus.RECEIVED,     # 重新從頭開始
        OrderStatus.CANCELLED,    # 放棄重試
    ],
    # 錯誤狀態：需人工介入後重試或取消
    OrderStatus.ERRORED: [
        OrderStatus.PENDING,      # 人工修復後重試
        OrderStatus.CANCELLED,    # 人工決定取消
    ],
    # 終端狀態：不可再轉移
    OrderStatus.CANCELLED: [],    # 已取消（終端）
    OrderStatus.SHIPPED: [],      # 已出貨（終端）try from failed
}


def can_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """
    Check if a status transition is valid.

    Args:
        from_status: Current order status
        to_status: Target status to transition to

    Returns:
        True if the transition is allowed, False otherwise.
    """
    allowed = ORDER_STATE_TRANSITIONS.get(from_status, [])
    return to_status in allowed


class Destination(SQLModel):
    """Destination model for order routing."""
    name: str = Field(..., description="Account name for order destination")


class Address(SQLModel):
    """Shipping address model."""
    name: Optional[str] = Field(None, description="Recipient name")
    companyName: Optional[str] = Field(None, description="Company name")
    address1: Optional[str] = Field(None, description="Street address")
    town: Optional[str] = Field(None, description="City/Town")
    state: Optional[str] = Field(None, description="State/Province")
    postcode: Optional[str] = Field(None, description="Postal code")
    isoCountry: Optional[str] = Field(None, description="ISO country code (e.g., US)")
    email: Optional[str] = Field(None, description="Contact email")
    phone: Optional[str] = Field(None, description="Contact phone")


class Carrier(SQLModel):
    """Shipping carrier model."""
    code: Optional[str] = Field(None, description="Carrier code (e.g., fedex)")
    service: Optional[str] = Field(None, description="Service level (e.g., ground)")
    serviceId: Optional[str] = Field(None, description="Service identifier")


class Shipment(SQLModel):
    """Shipment model."""
    shipTo: Optional[Address] = Field(None, description="Shipping address")
    carrier: Optional[Carrier] = Field(None, description="Carrier information")


class Color(SQLModel):
    """Color specification for print components."""
    side1: Optional[str] = Field(None, description="Front side color")
    side2: Optional[str] = Field(None, description="Back side color")


class Finish(SQLModel):
    """Finish specification for print components."""
    side1: Optional[str] = Field(None, description="Front side finish")
    side2: Optional[str] = Field(None, description="Back side finish")


class Component(SQLModel):
    """Component model for order items."""
    code: Optional[str] = Field(None, description="Component code")
    fetch: Optional[bool] = Field(True, description="Whether to fetch from URL")
    path: Optional[str] = Field(None, description="File path or URL")
    width: Optional[int] = Field(None, description="Width in pixels/dots")
    height: Optional[int] = Field(None, description="Height in pixels/dots")
    pages: Optional[int] = Field(None, description="Number of pages")
    paperId: Optional[str] = Field(None, description="Paper type identifier")
    attributes: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Custom attributes")
    colour: Optional[Color] = Field(None, description="Color specification")
    finish: Optional[Finish] = Field(None, description="Finish specification")


class OrderItem(SQLModel):
    """Order item model."""
    sku: str = Field(..., description="Product SKU")
    sourceItemId: Optional[str] = Field(None, description="External item ID")
    quantity: Optional[int] = Field(1, ge=1, description="Order quantity")
    printQuantity: Optional[int] = Field(None, ge=1, description="Print quantity")
    unitPrice: Optional[float] = Field(None, ge=0, description="Unit price")
    unitCost: Optional[float] = Field(None, ge=0, description="Unit cost")
    unitWeight: Optional[float] = Field(None, ge=0, description="Unit weight")
    productDescription: Optional[str] = Field(None, description="Product description")
    totalPages: Optional[int] = Field(None, ge=0, description="Total pages")
    components: Optional[List[Component]] = Field(None, sa_column=Column(JSON), description="Item components")


class OrderData(SQLModel):
    """Order data model containing order details."""
    sourceOrderId: str = Field(..., description="External order ID from source system")
    postbackAddress: Optional[str] = Field(None, description="Webhook callback URL")
    items: List[OrderItem] = Field(..., min_length=1, sa_column=Column(JSON), description="Order line items")
    shipments: Optional[List[Shipment]] = Field(None, sa_column=Column(JSON), description="Shipping information")


class Order(BaseModel, table=True):
    """Order model representing print orders in the system."""

    __tablename__ = "orders"

    order_id: str = Field(unique=True, index=True, nullable=False, description="Internal order ID (_id)")
    source_account: str = Field(nullable=False, index=True, description="Source account name")
    source_order_id: str = Field(nullable=False, index=True, description="External order ID from source")
    destination: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Destination information")
    source: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Source information")
    order_data: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Complete order data")
    status: OrderStatus = Field(default=OrderStatus.PENDING, nullable=False, description="Current order status")
    logs: Optional[List[Dict[str, Any]]] = Field(None, sa_column=Column(JSON), description="Order processing logs")
    files: Optional[List[Dict[str, Any]]] = Field(None, sa_column=Column(JSON), description="Associated files")
    version: int = Field(default=0, description="Document version (__v)")
    store_id: Optional[str] = Field(None, index=True, description="Store identifier")
    store_order_id: Optional[str] = Field(None, max_length=255, index=True, description="Store order ID for external reference")
