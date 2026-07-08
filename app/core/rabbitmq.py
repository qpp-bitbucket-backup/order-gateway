"""RabbitMQ connection and message publishing utilities."""
import json
import logging
import pika
from typing import Dict, Any
from app.core.config import settings

logger = logging.getLogger(__name__)
# Receive order from VFS and publish
QUEUE_ORDER_PUBLISHING = 'order_publishing'
# Validate order data and shipping address
QUEUE_ORDER_VALIDATING = 'order_validating'
# Push valid order to QPMN platform
QUEUE_ORDER_PUSHING = 'order_pushing'

def get_rabbitmq_connection_params() -> pika.ConnectionParameters:
    """Create RabbitMQ connection parameters from config."""
    credentials = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
    return pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        port=settings.RABBITMQ_PORT,
        virtual_host=settings.RABBITMQ_VHOST,
        credentials=credentials,
    )