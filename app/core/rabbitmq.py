"""RabbitMQ connection and message publishing utilities."""
import json
import logging
import pika
from typing import Dict, Any
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_rabbitmq_connection_params() -> pika.ConnectionParameters:
    """Create RabbitMQ connection parameters from config."""
    credentials = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
    return pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        port=settings.RABBITMQ_PORT,
        virtual_host=settings.RABBITMQ_VHOST,
        credentials=credentials,
    )


def publish_order_task(order_data: Dict[str, Any]) -> bool:
    """
    Publish an order processing task to RabbitMQ.

    Args:
        order_data: Dictionary containing order information to be processed.

    Returns:
        True if message was published successfully, False otherwise.
    """
    connection = None
    try:
        params = get_rabbitmq_connection_params()
        connection = pika.BlockingConnection(params)
        channel = connection.channel()

        # Declare queue (idempotent - creates if not exists)
        channel.queue_declare(
            queue=settings.RABBITMQ_ORDER_QUEUE,
            durable=True,
        )

        # Publish message
        channel.basic_publish(
            exchange="",
            routing_key=settings.RABBITMQ_ORDER_QUEUE,
            body=json.dumps(order_data, default=str),
            properties=pika.BasicProperties(
                delivery_mode=2,  # persistent message
                content_type="application/json",
            ),
        )

        logger.info(
            f"Order task published to queue '{settings.RABBITMQ_ORDER_QUEUE}': "
            f"order_id={order_data.get('order_id')}"
        )
        return True

    except pika.exceptions.AMQPConnectionError as e:
        logger.error(f"RabbitMQ connection error: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to publish order task to RabbitMQ: {e}")
        return False
    finally:
        if connection and connection.is_open:
            connection.close()
