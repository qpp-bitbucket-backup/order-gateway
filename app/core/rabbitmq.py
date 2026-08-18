"""RabbitMQ / Celery queue name constants and connection utilities."""
import logging
from urllib.parse import urlparse
import pika
from app.core.config import settings

logger = logging.getLogger(__name__)

# Receive order from VFS and publish
QUEUE_ORDER_PUBLISHING = 'order_publishing'
# Validate order data and shipping address
QUEUE_ORDER_VALIDATING = 'order_validating'
# Push valid order to QPMN platform
QUEUE_ORDER_PUSHING = 'order_pushing'
# Notify OMS/VFS of order status updates (outbound)
QUEUE_ORDER_NOTIFYING = 'order_notifying'


def get_rabbitmq_connection_params() -> pika.ConnectionParameters:
    """
    Create RabbitMQ connection parameters by parsing CELERY_BROKER_URL.

    Expected format: amqp://user:password@host:port/vhost
    """
    parsed = urlparse(settings.CELERY_BROKER_URL)

    username = parsed.username or "guest"
    password = parsed.password or "guest"
    host = parsed.hostname or "localhost"
    port = parsed.port or 5672
    vhost = parsed.path.lstrip("/") if parsed.path and parsed.path != "/" else "/"
    if not vhost:
        vhost = "/"
    credentials = pika.PlainCredentials(username, password)
    return pika.ConnectionParameters(
        host=host,
        port=port,
        virtual_host=vhost,
        credentials=credentials,
    )
