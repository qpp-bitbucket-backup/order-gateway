"""Order processing worker - consumes tasks from RabbitMQ order_processing queue."""
import json
import logging
import os
import tempfile
import pika
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlmodel import Session, select
from app.core.config import settings
from app.core.database import engine
from app.models.order import Order, OrderStatus, can_transition
from app.core.rabbitmq import get_rabbitmq_connection_params
from app.services.file import file_service
from app.services.client import client_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



def process_order_task(order_data: Dict[str, Any]) -> bool:
    """
    Process an order task from the queue.

    Args:
        order_data: Order data dictionary from the queue message.

    Returns:
        True if processing was successful, False otherwise.
    """
    order_id = order_data.get("order_id")
    logger.info(f"[Worker] Processing order task: order_id={order_id}")

    try:
        with Session(engine) as session:
            # Find the order
            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()

            if not order:
                logger.error(f"[Worker] Order not found: {order_id}")
                return False
            # Validate state transition: SUBMITTED -> PENDING
            if not can_transition(order.status, OrderStatus.PENDING):
                logger.error(
                    f"[Worker] Invalid state transition for order {order_id}: "
                    f"{order.status.value} -> {OrderStatus.PENDING.value}"
                )
                return False

            # Update order status to PENDING
            order.status = OrderStatus.PENDING
            
            # Add processing log
            if order.logs is None:
                order.logs = []
            
            order.logs.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "order_processing_started",
                "message": f"Order processing started by worker",
                "worker": "order_worker",
            })

            session.add(order)
            session.commit()

            logger.info(f"[Worker] Order {order_id} status updated to PENDING")

            # Collect all uploaded file records to save to order.files
            uploaded_files: List[Dict[str, Any]] = []

            # Process each product component file
            products = order.order_data.get("items", [])
            
            store_id = order.store_id
            store_key = client_service.get_store_key_by_id(store_id)
            with tempfile.TemporaryDirectory(prefix=f"order_{order_id}_") as tmp_dir:
                for product in products:
                    components = product.get("components", [])
                    for component in components:
                        file_url = component.get("path", None)
                        if not file_url:
                            continue

                        logger.info(f"[Worker] Processing file: {file_url}")

                        try:
                            # Step 1: Download file to local temp folder
                            success, result = file_service.download_file(file_url, tmp_dir)
                            if not success:
                                order.logs.append({
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "action": "download_order_files",
                                    "message": result,
                                    "worker": "order_worker",
                                })
                                order.status = OrderStatus.FAILED
                                session.add(order)
                                session.commit()
                                break

                            # Step 2: If PDF, split into single-page PDFs
                            if result.lower().endswith(".pdf"):
                                page_files = file_service.split_pdf(result, tmp_dir)
                            else:
                                page_files = [result]

                            # Step 3: Upload each file to QPMN API and collect URLs
                            for page_file in page_files:
                                upload_result = file_service.upload_to_qpmn(page_file, store_key)
                                if upload_result:
                                    uploaded_files.append(upload_result)

                        except Exception as file_err:
                            logger.error(
                                f"[Worker] Failed to process file {file_url}: {file_err}",
                                exc_info=True,
                            )

                # Save uploaded file records to order.files
                if uploaded_files:
                    order.files = uploaded_files
                    session.add(order)
                    session.commit()
                    logger.info(
                        f"[Worker] Saved {len(uploaded_files)} file(s) to order {order_id}"
                    )
                    return True
            # - Validate files
            # - Send to production system
            # - Update tracking information
            # - Send webhooks

            return False

    except Exception as e:
        logger.error(f"[Worker] Failed to process order {order_id}: {e}", exc_info=True)
        
        # Try to update order status to FAILED
        try:
            with Session(engine) as session:
                order = session.exec(
                    select(Order).where(Order.order_id == order_id)
                ).first()
                
                if order:
                    order.status = OrderStatus.FAILED
                    if order.logs is None:
                        order.logs = []
                    
                    order.logs.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "action": "order_processing_failed",
                        "message": f"Processing failed: {str(e)}",
                        "worker": "order_worker",
                    })
                    
                    session.add(order)
                    session.commit()
        except Exception as log_error:
            logger.error(f"[Worker] Failed to update order status to FAILED: {log_error}")
        
        return False


def on_message_received(channel, method, properties, body):
    """
    Callback function when a message is received from the queue.

    Args:
        channel: Pika channel object
        method: Delivery method with routing info
        properties: Message properties
        body: Message body (bytes)
    """
    try:
        # Parse message
        order_data = json.loads(body.decode("utf-8"))
        logger.info(f"[Worker] Received order task: {order_data.get('order_id')}")

        # Process the order
        success = process_order_task(order_data)

        if success:
            # Acknowledge the message (remove from queue)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(f"[Worker] Order task completed successfully: {order_data.get('order_id')}")
        else:
            # Reject and requeue the message for retry
            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=True
            )
            logger.warning(f"[Worker] Order task failed, requeued: {order_data.get('order_id')}")

    except json.JSONDecodeError as e:
        logger.error(f"[Worker] Invalid JSON message: {e}")
        # Reject malformed messages (don't requeue)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except Exception as e:
        logger.error(f"[Worker] Error processing message: {e}", exc_info=True)
        # Reject and requeue for retry
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def start_worker():
    """Start the order processing worker."""
    logger.info("[Worker] Starting order processing worker...")
    logger.info(f"[Worker] Connecting to RabbitMQ: {settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}")
    logger.info(f"[Worker] Queue: {settings.RABBITMQ_ORDER_QUEUE}")

    connection = None
    try:
        # Connect to RabbitMQ
        params = get_rabbitmq_connection_params()
        connection = pika.BlockingConnection(params)
        channel = connection.channel()

        # Declare queue (idempotent)
        channel.queue_declare(
            queue=settings.RABBITMQ_ORDER_QUEUE,
            durable=True,
        )

        # Set prefetch count to 1 (process one message at a time)
        channel.basic_qos(prefetch_count=1)

        # Start consuming
        channel.basic_consume(
            queue=settings.RABBITMQ_ORDER_QUEUE,
            on_message_callback=on_message_received,
            auto_ack=False,
        )

        logger.info("[Worker] Worker started successfully. Waiting for messages...")
        channel.start_consuming()

    except pika.exceptions.AMQPConnectionError as e:
        logger.error(f"[Worker] Failed to connect to RabbitMQ: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("[Worker] Worker stopped by user")
        if connection and connection.is_open:
            connection.close()
    except Exception as e:
        logger.error(f"[Worker] Worker error: {e}", exc_info=True)
        if connection and connection.is_open:
            connection.close()
        raise


if __name__ == "__main__":
    process_order_task({"order_id": "814e3b9f-d741-411f-bd08-8db139edd9e4", "source_order_id": "ORD-0000003", "status": "submitted", "created_at": "2026-06-24T07:03:41"})
    # start_worker()
