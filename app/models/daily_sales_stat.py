"""Daily sales statistics model.

One row per (client, UTC day), written by the Celery beat task
``tasks.stats.aggregate_daily_sales_stats`` for frontend sales dashboards.
"""
from datetime import date
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field

from app.models.base import BaseModel


class DailySalesStat(BaseModel, table=True):
    """Daily sales statistics for one client (store)."""

    __tablename__ = "daily_sales_stats"
    __table_args__ = (
        UniqueConstraint("client_id", "stat_date", name="uq_daily_sales_stats_client_date"),
        Index("ix_daily_sales_stats_stat_date", "stat_date"),
    )

    stat_date: date = Field(..., description="UTC day the statistics cover")
    client_id: int = Field(
        ...,
        foreign_key="clients.id",
        index=True,
        description="Client (store) the statistics belong to",
    )
    store_id: str = Field(
        ..., max_length=128, index=True,
        description="Denormalized store_id (clients.store_id) for direct queries",
    )
    currency: Optional[str] = Field(
        None, max_length=8,
        description="Store currency code (QPMN); total_amount is in this currency",
    )

    orders_requested: int = Field(
        0, description="Order requests created that day (all statuses)",
    )
    orders_submitted: int = Field(
        0,
        description="Orders of that day successfully submitted to QPMN (store_order_id set)",
    )
    line_items_count: int = Field(
        0, description="Line items across the successfully submitted orders",
    )
    line_items_quantity: int = Field(
        0, description="Total item quantity across the successfully submitted orders",
    )
    total_amount: float = Field(
        0.0,
        description="Sales amount (sum of unitPrice * quantity) across the successfully submitted orders",
    )
