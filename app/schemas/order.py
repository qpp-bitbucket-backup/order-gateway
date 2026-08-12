from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union


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
    localFile: Optional[bool] = Field(None, description="Whether the file is local (VFS request field)")
    extraData: Optional[List[Any]] = Field(None, description="VFS-provided extra data, passthrough")


class OrderItem(BaseModel):
    """Order item schema."""
    sku: str = Field(..., description="Product SKU")
    sourceItemId: Optional[Union[str, int]] = Field(None, description="External item ID — VFS sends this as an integer")
    quantity: Optional[int] = Field(1, ge=1, description="Order quantity")
    printQuantity: Optional[int] = Field(None, ge=1, description="Print quantity")
    unitPrice: Optional[float] = Field(None, ge=0, description="Unit price")
    unitCost: Optional[float] = Field(None, ge=0, description="Unit cost")
    unitWeight: Optional[float] = Field(None, ge=0, description="Unit weight")
    productDescription: Optional[str] = Field(None, description="Product description")
    totalPages: Optional[int] = Field(None, ge=0, description="Total pages")
    components: Optional[List[Component]] = Field(None, description="Item components")
    description: Optional[str] = Field(None, description="Item description (VFS request field)")
    pages: Optional[int] = Field(None, ge=0, description="Page count (VFS request field)")
    shipmentIndex: Optional[int] = Field(None, description="Index into orderData.shipments this item ships in")
    extraData: Optional[List[Any]] = Field(None, description="VFS-provided extra data, passthrough")


class OrderData(BaseModel):
    """Order data schema containing order details."""
    sourceOrderId: str = Field(..., description="External order ID from source system")
    postbackAddress: Optional[str] = Field(None, description="Webhook callback URL")
    items: List[OrderItem] = Field(..., min_length=1, description="Order line items")
    shipments: Optional[List[Shipment]] = Field(None, description="Shipping information")
    stockItems: Optional[List[Any]] = Field(None, description="VFS stock items, passthrough")
    error: Optional[List[Any]] = Field(None, description="VFS error list, passthrough")
    extraData: Optional[List[Any]] = Field(None, description="VFS-provided extra data, passthrough")
    printType: Optional[str] = Field(None, description="Print type (e.g. digital)")
    email: Optional[str] = Field(None, description="Customer email")
    amount: Optional[float] = Field(None, description="Order amount")
    customerName: Optional[str] = Field(None, description="Customer name")


class OrderValidationRequest(BaseModel):
    """Schema for order validation request."""
    destination: Destination = Field(..., description="Order destination")
    orderData: OrderData = Field(..., description="Order data to validate")
    source: Optional[Dict[str, Any]] = Field(None, description="Source system info (VFS top-level field)")
    files: Optional[List[Any]] = Field(None, description="Top-level files list (VFS top-level field)")


class OrderSubmissionRequest(BaseModel):
    """Schema for order submission request."""
    destination: Destination = Field(..., description="Order destination")
    orderData: OrderData = Field(..., description="Order data to submit")


class FullOrder(BaseModel):
    """Complete order response schema."""
    id: str = Field(..., alias="_id", description="Internal order ID")
    destination: Optional[Dict[str, Any]] = Field(None, description="Destination information")
    source: Optional[Dict[str, Any]] = Field(None, description="Source information")
    orderData: Optional[Dict[str, Any]] = Field(None, description="Complete order data")
    version: int = Field(1, alias="__v", description="Document version")

    class Config:
        populate_by_name = True


class OrderValidationResponse(BaseModel):
    """Schema for order validation response.

    VFS requires ``orderData`` at the top level (at minimum ``sourceOrderId``
    nested inside it) — no ``success``/``order`` wrapper.
    """
    orderData: Dict[str, Any] = Field(..., description="Validated order data")


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


class PlatformOrderSummary(BaseModel):
    """Platform order summary with additional fields."""
    id: str = Field(..., alias="_id", description="Internal order ID")
    sourceOrderId: str = Field(..., description="External order ID from source")
    destination: Optional[Dict[str, Any]] = Field(None, description="Destination information")
    source: Optional[Dict[str, Any]] = Field(None, description="Source information")
    orderData: Optional[Dict[str, Any]] = Field(None, description="Order data summary")
    status: str = Field(..., description="Current order status")
    logs: Optional[Any] = Field(None, description="Order processing logs")
    files: Optional[Any] = Field(None, description="Associated files")
    version: int = Field(0, description="Document version")
    storeId: Optional[str] = Field(None, description="Store identifier")
    storeOrderId: Optional[str] = Field(None, description="Store order ID")
    createdAt: Optional[str] = Field(None, description="Created timestamp")
    updatedAt: Optional[str] = Field(None, description="Updated timestamp")

    class Config:
        populate_by_name = True


class PlatformOrdersListResponse(BaseModel):
    """Schema for platform orders list response."""
    success: bool = Field(..., description="Request success status")
    count: int = Field(..., description="Total number of orders")
    page: int = Field(..., description="Current page number")
    pages: int = Field(..., description="Total number of pages")
    data: List[PlatformOrderSummary] = Field(..., description="List of order summaries")


class MaskedAddress(BaseModel):
    """Masked address schema for PII protection."""
    first_name: Optional[str] = Field(None, description="Masked first name")
    last_name: Optional[str] = Field(None, description="Masked last name")
    phone: Optional[str] = Field(None, description="Masked phone number")
    mobile: Optional[str] = Field(None, description="Masked mobile number")
    email: Optional[str] = Field(None, description="Masked email address")
    address1: Optional[str] = Field(None, description="Masked street address")
    address2: Optional[str] = Field(None, description="Masked street address line 2")
    postcode: Optional[str] = Field(None, description="Masked postal code")
    city: Optional[str] = Field(None, description="City (not masked)")
    state: Optional[str] = Field(None, description="State (not masked)")
    country: Optional[str] = Field(None, description="Country (not masked)")
    company: Optional[str] = Field(None, description="Masked company name")


class PlatformFullOrder(BaseModel):
    """Complete platform order with all fields."""
    id: str = Field(..., alias="_id", description="Internal order ID")
    sourceOrderId: str = Field(..., description="External order ID from source")
    destination: Optional[Dict[str, Any]] = Field(None, description="Destination information")
    source: Optional[Dict[str, Any]] = Field(None, description="Source information")
    orderData: Optional[Dict[str, Any]] = Field(None, description="Complete order data")
    status: str = Field(..., description="Current order status")
    logs: Optional[Any] = Field(None, description="Order processing logs")
    files: Optional[Any] = Field(None, description="Associated files")
    version: int = Field(0, description="Document version")
    storeId: Optional[str] = Field(None, description="Store identifier")
    storeOrderId: Optional[str] = Field(None, description="Store order ID")
    createdAt: Optional[str] = Field(None, description="Created timestamp")
    updatedAt: Optional[str] = Field(None, description="Updated timestamp")
    deliveryAddress: Optional[MaskedAddress] = Field(None, description="Latest delivery address with PII masked")
    billingAddress: Optional[MaskedAddress] = Field(None, description="Latest billing address with PII masked")

    class Config:
        populate_by_name = True


class PlatformOrderDetailsResponse(BaseModel):
    """Schema for platform order details response."""
    success: bool = Field(True, description="Request success status")
    order: Optional[PlatformFullOrder] = Field(None, description="Complete order details")


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


class SiteFlowErrorDetail(BaseModel):
    """SiteFlow-compatible error detail object."""
    message: str = Field(..., description="Human-readable error message")
    name: str = Field(..., description="Error name/classifier")
    code: int = Field(..., description="HTTP status code")


class SiteFlowErrorResponse(BaseModel):
    """SiteFlow-compatible error response wrapper."""
    success: bool = Field(False, description="Always false for errors")
    error: SiteFlowErrorDetail = Field(..., description="Error detail")


class OrderUpdateRequest(BaseModel):
    """Schema for order update request.

    Only fields provided will be updated. The order must be in a cancellable
    state (received, pending, validated, failed, errored) to allow updates.
    """
    destination: Optional[Destination] = Field(None, description="Updated destination information")
    orderData: Optional[OrderData] = Field(None, description="Updated order data (replaces existing)")


class OrderUpdateResponse(BaseModel):
    """Schema for order update response."""
    success: bool = Field(True, description="Update success status")
    message: str = Field("Order updated successfully", description="Update message")
    order: Optional[FullOrder] = Field(None, description="Updated order details")
