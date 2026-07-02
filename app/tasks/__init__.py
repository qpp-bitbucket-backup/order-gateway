"""Celery tasks package."""
from app.tasks.products import sync_products_from_qpmn

__all__ = ["sync_products_from_qpmn"]
