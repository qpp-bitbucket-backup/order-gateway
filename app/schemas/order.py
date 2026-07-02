from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class Destination(BaseModel):
    """Destination schema for order routing."""
    name: str = Field(..., description="Account name for order destination")


class Address(BaseModel):
    """Shipping address schema."""
    name: Optional[str] = Field(None, description="Recipient name")
    companyName: Optional[str] = Field(None, description="Company name")
    address1: Optional[str] = Field(None, description="Street address")
    town: Optional[str] = Field(None, description="City/Town")
    state: Optional[str] = Field(None, description="State/Province")
    postcode: Optional[str] = Field(None, description="Postal code")
    isoCountry: Optional[str] = Field(None, description="ISO country code (e.g., US)")
    email: Optional[str] = Field(None, description="Contact email")
    phone: Optional[str] = Field(None, description="Contact phone")


class Carrier(BaseModel):
    """Shipping carrier schema."""
    code: Optional[str] = Field(None, description="Carrier code (e.g., fedex)")
    service: Optional[str] = Field(None, description="Service level (e.g., ground)")
    serviceId: Optional[str] = Field(None, description="Service identifier")


class Shipment(BaseModel):
    """Shipment schema."""
    shipTo: Optional[Address] = Field(None, description="Shipping address")
    carrier: Optional[Carrier] = Field(None, description="Carrier information")


class Color(BaseModel):
    """Color specification for print components."""
    side1: Optional[str] = Field(None, description="Front side color")
    side2: Optional[str] = Field(None, description="Back side color")


class Finish(BaseModel):
    """Finish specification for print components."""
    side1: Optional[str] = Field(None, description="Front side finish")
    side2: Optional[str] = Field(None, description="Back side finish")


class Component(BaseModel):
    """Component schema for order items."""
    code: Optional[str] = Field(None, description="Component code")
    fetch: Optional[bool] = Field(True, description="Whether to fetch from URL")
    path: Optional[str] = Field(None, description="File path or URL")
    width: Optional[int] = Field(None, description="Width in pixels/dots")
    height: Optional[int] = Field(None, description="Height in pixels/dots")
    pages: Optional[int] = Field(None, description="Number of pages")
    paperId: Optional[str] = Field(None, description="Paper type identifier")
    attributes: Optional[Dict[str, Any]] = Field(None, description="Custom attributes")
    colour: Optional[Color] = Field(None, description="Color specification")
    finish: Optional[Finish] = Field(None, description="Finish specification")


class OrderItem(BaseModel):
    """Order item schema."""
    sku: str = Field(..., description="Product SKU")
    sourceItemId: Optional[str] = Field(None, description="External item ID")
    quantity: Optional[int] = Field(1, ge=1, description="Order quantity")
    printQuantity: Optional[int] = Field(None, ge=1, description="Print quantity")
    unitPrice: Optional[float] = Field(None, ge=0, description="Unit price")
    unitCost: Optional[float] = Field(None, ge=0, description="Unit cost")
    unitWeight: Optional[float] = Field(None, ge=0, description="Unit weight")
    productDescription: Optional[str] = Field(None, description="Product description")
    totalPages: Optional[int] = Field(None, ge=0, description="Total pages")
    components: Optional[List[Component]] = Field(None, description="Item components")


class OrderData(BaseModel):
    """Order data schema containing order details."""
    sourceOrderId: str = Field(..., description="External order ID from source system")
    postbackAddress: Optional[str] = Field(None, description="Webhook callback URL")
    items: List[OrderItem] = Field(..., min_length=1, description="Order line items")
    shipments: Optional[List[Shipment]] = Field(None, description="Shipping information")


class OrderValidationRequest(BaseModel):
    """Schema for order validation request."""
    destination: Destination = Field(..., description="Order destination")
    orderData: OrderData = Field(..., description="Order data to validate")


class OrderSubmissionRequest(BaseModel):
    """Schema for order submission request."""
    destination: Destination = Field(..., description="Order destination")
    orderData: OrderData = Field(..., description="Order data to submit")


class FullOrder(BaseModel):
    """Complete order response schema."""
    id: str = Field(..., alias="_id", description="Internal order ID")
    v: int = Field(0, alias="__v", description="Document version")
    destination: Optional[Dict[str, Any]] = Field(None, description="Destination information")
    source: Optional[Dict[str, Any]] = Field(None, description="Source information")
    orderData: Optional[Dict[str, Any]] = Field(None, description="Complete order data")
    logs: Optional[List[Dict[str, Any]]] = Field(None, description="Order processing logs")
    files: Optional[List[Dict[str, Any]]] = Field(None, description="Associated files")

    class Config:
        populate_by_name = True


class OrderValidationResponse(BaseModel):
    """Schema for order validation response."""
    success: bool = Field(..., description="Validation success status")
    order: Optional[Dict[str, Any]] = Field(None, description="Validated order data")


class OrderSubmissionResponse(BaseModel):
    """Schema for order submission response."""
    success: bool = Field(..., description="Submission success status")
    order: Optional[FullOrder] = Field(None, description="Submitted order details")


class OrderSummary(BaseModel):
    """Order summary for list responses."""
    id: str = Field(..., alias="_id", description="Internal order ID")
    destination: Optional[Dict[str, Any]] = Field(None, description="Destination information")
    source: Optional[Dict[str, Any]] = Field(None, description="Source information")
    orderData: Optional[Dict[str, Any]] = Field(None, description="Order data summary")

    class Config:
        populate_by_name = True


class OrdersListResponse(BaseModel):
    """Schema for orders list response."""
    success: bool = Field(..., description="Request success status")
    count: int = Field(..., description="Total number of orders")
    page: int = Field(..., description="Current page number")
    pages: int = Field(..., description="Total number of pages")
    data: List[OrderSummary] = Field(..., description="List of order summaries")


class OrderDetailsResponse(BaseModel):
    """Schema for detailed order response."""
    success: bool = Field(True, description="Request success status")
    order: Optional[FullOrder] = Field(None, description="Complete order details")


class CancelledOrderResponse(BaseModel):
    """Schema for cancelled order response."""
    success: bool = Field(True, description="Cancellation success status")
    message: str = Field("Order cancelled successfully", description="Cancellation message")
    order_id: Optional[str] = Field(None, description="Cancelled order ID")
