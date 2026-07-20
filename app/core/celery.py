"""Celery application configuration."""
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings
from app.core.rabbitmq import QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING, QUEUE_ORDER_NOTIFYING

# All order processing queues
ORDER_QUEUES = [QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING, QUEUE_ORDER_NOTIFYING]

# Create Celery app
celery_app = Celery(
    "order_gateway",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes timeout
    worker_prefetch_multiplier=1,
    # Retry policy is declared per task (autoretry_for/max_retries/default_retry_delay
    # are task decorator options; Celery has no global equivalents).
    # Route order tasks to their dedicated queues
    task_routes={
        "tasks.orders.publish_order": {"queue": QUEUE_ORDER_PUBLISHING},
        "tasks.orders.validate_order": {"queue": QUEUE_ORDER_VALIDATING},
        "tasks.orders.push_order": {"queue": QUEUE_ORDER_PUSHING},
        "tasks.notifications.notify_oms": {"queue": QUEUE_ORDER_NOTIFYING},
        "tasks.notifications.notify_vfs": {"queue": QUEUE_ORDER_NOTIFYING},
    },
    # Celery Beat schedule for periodic tasks
    beat_schedule={
        # Sync products for all stores at configured interval
        "sync-products-periodic": {
            "task": "tasks.products.sync_all_stores_products",
            "schedule": crontab(minute=f"*/{settings.CELERY_BEAT_SYNC_PRODUCT_INTERVAL_MINUTES}"),
        } if settings.CELERY_BEAT_SYNC_ENABLED else {},
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks", "app.tasks.orders", "app.tasks.notifications"])


@celery_app.task(bind=True)
def debug_task(self):
    """Debug task to test Celery setup."""
    return f"Celery is working! Task ID: {self.request.id}"
