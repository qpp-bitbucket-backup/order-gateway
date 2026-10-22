"""Daily sales statistics aggregation.

Beat task that aggregates per-store sales metrics into ``daily_sales_stats``
(one row per client per UTC day) for frontend dashboards.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlmodel import Session, select

from app.core.celery import celery_app
from app.core.database import engine
from app.models.client import Client
from app.models.daily_sales_stat import DailySalesStat
from app.models.order import Order

logger = logging.getLogger(__name__)

# Recompute the trailing window (yesterday + 2 prior days) on every run so
# orders accepted by QPMN shortly after midnight still land on their
# creation day. Upserts make reruns idempotent.
RECOMPUTE_WINDOW_DAYS = 3


def _iter_order_items(order: Order) -> Iterator[dict]:
    """Yield raw item dicts from order_data (tolerant of missing/odd fields)."""
    items = (order.order_data or {}).get("items") or []
    for item in items:
        if isinstance(item, dict):
            yield item


def _extract_subtotal(payload: Optional[dict]) -> Optional[float]:
    """Pull the pre-computed order subtotal from a creation_payload.

    Two supported shapes, both produced by ``order_service.build_push_payload``:

    * Open API:  ``priceInfo.subtotal``
    * Legacy API: ``orderTotals`` list, entry whose ``name == "SUBTOTAL"``

    Returns None when neither shape carries a numeric subtotal.
    """
    if not payload:
        return None
    # Open API shape
    price_info = payload.get("priceInfo")
    if isinstance(price_info, dict):
        value = price_info.get("subtotal")
        if isinstance(value, (int, float)):
            return float(value)
    # Legacy shape
    for entry in payload.get("orderTotals") or []:
        if isinstance(entry, dict) and entry.get("name") == "SUBTOTAL":
            value = entry.get("value")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _sum_items(items: Optional[list]) -> float:
    """Sum ``unitPrice * quantity`` (Open API) or ``unitPrice * qty`` (legacy)
    across a creation_payload's items list. Unknown shape → 0.0.
    """
    total = 0.0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        price = item.get("unitPrice") or 0
        if not isinstance(price, (int, float)):
            continue
        qty = item.get("quantity") or item.get("qty") or 0
        if not isinstance(qty, (int, float)):
            continue
        total += float(price) * float(qty)
    return round(total, 2)


def _get_order_amount(order: Order) -> float:
    """Resolve an order's monetary amount, trying sources in order of trust:

    1. ``creation_payload.priceInfo.subtotal`` / ``orderTotals SUBTOTAL``
       — gateway-computed subtotal, always uses ``sku.unit_price``.
    2. ``creation_payload.items[].unitPrice * quantity/qty``
       — same data recomputed per line item.
    3. ``order_data.items[].unitPrice * quantity``
       — original external order data; ``unitPrice`` is often ``None`` here,
         which is why steps 1–2 exist.

    Returns 0.0 when every source is empty.
    """
    payload = order.creation_payload
    # (1) Prefer the pre-computed subtotal on the payload.
    subtotal = _extract_subtotal(payload)
    if subtotal is not None:
        return subtotal
    # (2) Recompute from the payload's line items (unitPrice populated by SKU lookup).
    if payload:
        from_payload = _sum_items(payload.get("items"))
        if from_payload > 0:
            return from_payload
    # (3) Last resort: original order_data (unitPrice usually None → 0).
    return _sum_items((order.order_data or {}).get("items"))


def _fetch_currency(store_id: str) -> Optional[str]:
    """Fetch the store currency, importing order_service lazily.

    app.services.order imports app.tasks (via tasks/orders.py), which runs
    this package's __init__ — importing order_service at module level here
    would create a circular import when services.order is the entry point.
    """
    from app.services.order import order_service
    return order_service.fetch_currency_from_qpmn(store_id) or None


def aggregate_day(
    session: Session,
    day: date,
    client_by_store: Dict[Optional[str], Client],
    currency_cache: Dict[str, Optional[str]],
    only_store_id: Optional[str] = None,
) -> int:
    """Aggregate one UTC day into daily_sales_stats (upsert per client).

    Returns the number of stat rows written/updated. When ``only_store_id``
    is given, orders from other stores are excluded from the aggregation
    (used by the manual backfill script to target a single store).
    """
    # BaseModel.created_at is timezone-aware (Python datetime with tzinfo),
    # so query boundaries must also carry tzinfo — MySQL DATETIME stores no
    # zone, but SQLModel validates bind params against the column type.
    day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)

    order_query = select(Order).where(
        Order.is_active.is_(True),
        Order.created_at >= day_start,
        Order.created_at < day_end,
    )
    if only_store_id is not None:
        order_query = order_query.where(Order.store_id == only_store_id)
    orders = session.exec(order_query).all()

    by_store: Dict[Optional[str], List[Order]] = {}
    for order in orders:
        by_store.setdefault(order.store_id, []).append(order)

    rows_written = 0
    for store_id, store_orders in by_store.items():
        client = client_by_store.get(store_id)
        if client is None:
            logger.warning(
                "[SalesStats] %s: skipping %s order(s) with store_id=%r (no client row)",
                day, len(store_orders), store_id,
            )
            continue

        submitted = [o for o in store_orders if o.store_order_id]
        items_count = 0
        items_quantity = 0
        total_amount = 0.0
        for order in submitted:
            for item in _iter_order_items(order):
                items_count += 1
                qty = item.get("quantity") or 0
                items_quantity += qty
            total_amount += _get_order_amount(order)

        if store_id not in currency_cache:
            # One QPMN lookup per store per run; falls back to "CNY" on error
            # (same behavior as the currency fetch during order push).
            currency_cache[store_id] = _fetch_currency(store_id)

        stat = session.exec(
            select(DailySalesStat).where(
                DailySalesStat.client_id == client.id,
                DailySalesStat.stat_date == day,
            )
        ).first()
        if stat is None:
            stat = DailySalesStat(stat_date=day, client_id=client.id, store_id=store_id)

        stat.orders_requested = len(store_orders)
        stat.orders_submitted = len(submitted)
        stat.line_items_count = items_count
        stat.line_items_quantity = items_quantity
        stat.total_amount = round(total_amount, 2)
        stat.currency = currency_cache[store_id]
        stat.updated_at = datetime.now(timezone.utc)
        session.add(stat)
        rows_written += 1

    session.commit()
    return rows_written


@celery_app.task(bind=True, name="tasks.stats.aggregate_daily_sales_stats")
def aggregate_daily_sales_stats(self, days: Optional[int] = None) -> Dict[str, Any]:
    """Aggregate daily sales stats for the trailing window (default 3 days).

    Runs daily via Celery beat and can also be triggered manually with a
    larger ``days`` window to backfill history (e.g. days=30).
    """
    window = days or RECOMPUTE_WINDOW_DAYS
    today = datetime.now(timezone.utc).date()
    targets = [today - timedelta(days=n) for n in range(1, window + 1)]

    with Session(engine) as session:
        # Inactive clients keep their attribution — historical stats stay
        # queryable by client_id even after a client is deactivated.
        clients = session.exec(select(Client)).all()
        client_by_store = {c.store_id: c for c in clients if c.store_id}
        currency_cache: Dict[str, Optional[str]] = {}

        results = {}
        for day in targets:
            results[str(day)] = aggregate_day(session, day, client_by_store, currency_cache)

    logger.info("[SalesStats] aggregated %s -> %s", targets, results)
    return {"success": True, "days": results, "timestamp": datetime.now(timezone.utc).isoformat()}
