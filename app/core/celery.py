"""Celery application configuration."""
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings
from app.core.rabbitmq import QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING

# All order processing queues
ORDER_QUEUES = [QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING]

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
    # Retry configuration
    task_autoretry_for=(Exception,),
    task_max_retries=3,
    task_default_retry_delay=60,  # 60 seconds between retries
    # Route order tasks to their dedicated queues
    task_routes={
        "tasks.orders.publish_order": {"queue": QUEUE_ORDER_PUBLISHING},
        "tasks.orders.validate_order": {"queue": QUEUE_ORDER_VALIDATING},
        "tasks.orders.push_order": {"queue": QUEUE_ORDER_PUSHING},
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
celery_app.autodiscover_tasks(["app.tasks", "app.tasks.orders"])


@celery_app.task(bind=True)
def debug_task(self):
    """Debug task to test Celery setup."""
    return f"Celery is working! Task ID: {self.request.id}"
