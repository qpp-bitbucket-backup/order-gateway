from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.models.base import BaseModel


class OrderShipment(BaseModel, table=True):
    """A shipment/tracking record for an order, populated from QPMN's
    ``package_shipped`` webhook event. Previously this data only lived
    inside ``webhook_logs.payload`` (raw JSON, not queryable) — this table
    gives GET /order/details a real source to join against."""

    __tablename__ = "order_shipments"

    order_id: str = Field(nullable=False, index=True, foreign_key="orders.order_id", description="Internal order ID")
    store_order_id: Optional[str] = Field(None, index=True, max_length=64, description="Store order ID (QPMN orderId)")
    qpmn_shipment_id: Optional[str] = Field(None, index=True, max_length=64, description="QPMN's shipment id")
    shipment_index: Optional[int] = Field(None, description="Index linking this shipment to order_data.shipments / items")
    tracking_number: Optional[str] = Field(None, max_length=128, description="Carrier tracking number")
    tracking_url: Optional[str] = Field(None, max_length=512, description="Tracking URL")
    carrier: Optional[str] = Field(None, max_length=64, description="Shipping carrier (QPMN's 'company' field)")
    ship_date: Optional[datetime] = Field(None, description="Ship date")
    items: Optional[List[Dict[str, Any]]] = Field(None, sa_column=Column(JSON), description="Items in this shipment: [{itemId, quantity}]")
