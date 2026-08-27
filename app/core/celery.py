"""Celery application configuration."""
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings
from app.core.monitoring import init_sentry
from app.core.rabbitmq import QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING, QUEUE_ORDER_NOTIFYING, QUEUE_PRODUCT_SYNCING

# Initialize Sentry in every process launched via `-A app.core.celery.celery_app`
# (worker and beat). These processes never run the FastAPI lifespan in
# main.py, and capture_integration_alert() in tasks is a silent no-op unless
# the SDK was initialized — alert events were getting dropped here.
# CeleryIntegration (enabled inside init_sentry) makes this fork-safe for
# prefork workers. Re-init in the API process (lifespan) is harmless.
init_sentry()

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
        # Product/SKU sync tasks (manual triggers + beat periodic) get
        # their own queue so they never land in the default "celery" queue
        # and can be consumed/scaled independently from order flow.
        "tasks.products.sync_products_from_qpmn": {"queue": QUEUE_PRODUCT_SYNCING},
        "tasks.products.sync_skus_from_qpmn": {"queue": QUEUE_PRODUCT_SYNCING},
        "tasks.products.sync_all_stores_products": {"queue": QUEUE_PRODUCT_SYNCING},
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
