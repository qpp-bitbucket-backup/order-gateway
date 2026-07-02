"""Health check utilities for database and RabbitMQ connectivity."""
import logging
from typing import Dict, Any
from datetime import datetime,timezone
from sqlmodel import Session, select, text
from app.core.database import engine
from app.core.rabbitmq import get_rabbitmq_connection_params
import pika

logger = logging.getLogger(__name__)


def check_database_health() -> Dict[str, Any]:
    """
    Check database connectivity and basic query execution.

    Returns:
        Dictionary with database health status and details.
    """
    try:
        start_time = datetime.now(timezone.utc)
        
        with Session(engine) as session:
            # Execute a simple query to verify connection
            result = session.exec(text("SELECT 1")).first()
            
            if result is None:
                return {
                    "status": "unhealthy",
                    "message": "Database query returned no result",
                    "response_time_ms": 0,
                }
        
        end_time = datetime.now(timezone.utc)
        response_time_ms = (end_time - start_time).total_seconds() * 1000
        
        return {
            "status": "healthy",
            "message": "Database connection successful",
            "response_time_ms": round(response_time_ms, 2),
        }
        
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "message": f"Database connection failed: {str(e)}",
            "response_time_ms": 0,
        }


def check_rabbitmq_health() -> Dict[str, Any]:
    """
    Check RabbitMQ connectivity by attempting to open a connection.

    Returns:
        Dictionary with RabbitMQ health status and details.
    """
    connection = None
    try:
        start_time = datetime.now(timezone.utc)
        
        params = get_rabbitmq_connection_params()
        connection = pika.BlockingConnection(params)
        
        if not connection.is_open:
            return {
                "status": "unhealthy",
                "message": "RabbitMQ connection could not be opened",
                "response_time_ms": 0,
            }
        
        # Verify channel can be created
        channel = connection.channel()
        channel.close()
        
        end_time = datetime.now(timezone.utc)
        response_time_ms = (end_time - start_time).total_seconds() * 1000
        
        return {
            "status": "healthy",
            "message": "RabbitMQ connection successful",
            "response_time_ms": round(response_time_ms, 2),
        }
        
    except pika.exceptions.AMQPConnectionError as e:
        logger.error(f"RabbitMQ health check failed (connection error): {e}")
        return {
            "status": "unhealthy",
            "message": f"RabbitMQ connection failed: {str(e)}",
            "response_time_ms": 0,
        }
    except Exception as e:
        logger.error(f"RabbitMQ health check failed: {e}")
        return {
            "status": "unhealthy",
            "message": f"RabbitMQ check failed: {str(e)}",
            "response_time_ms": 0,
        }
    finally:
        if connection and connection.is_open:
            connection.close()


def get_full_health_status() -> Dict[str, Any]:
    """
    Get comprehensive health status including all dependencies.

    Returns:
        Dictionary with overall health status and individual component statuses.
    """
    db_health = check_database_health()
    rabbitmq_health = check_rabbitmq_health()
    
    # Overall status is healthy only if all components are healthy
    overall_status = "healthy" if (
        db_health["status"] == "healthy" and 
        rabbitmq_health["status"] == "healthy"
    ) else "degraded"
    
    return {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "database": db_health,
            "rabbitmq": rabbitmq_health,
        }
    }
