"""Sentry and SkyWalking monitoring initialization."""
import logging
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """
    Initialize Sentry for error tracking and performance monitoring.

    Returns:
        True if Sentry was initialized successfully, False otherwise.
    """
    if not settings.SENTRY_DSN:
        logger.info("Sentry DSN not configured, skipping Sentry initialization")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            environment=settings.SENTRY_ENVIRONMENT,
            send_default_pii=False,  # Don't send personal data
        )

        logger.info(
            f"Sentry initialized successfully (environment={settings.SENTRY_ENVIRONMENT})"
        )
        return True

    except ImportError:
        logger.error("sentry-sdk not installed. Run: pip install sentry-sdk[fastapi]")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")
        return False


def init_skywalking() -> bool:
    """
    Initialize Apache SkyWalking APM agent for distributed tracing.

    Returns:
        True if SkyWalking was initialized successfully, False otherwise.
    """
    if not settings.SKYWALKING_ENABLED:
        logger.info("SkyWalking is disabled, skipping initialization")
        return False

    try:
        from skywalking import agent, config
        config.init(
            agent_collector_backend_services=settings.SKYWALKING_BACKEND_ADDRESS,
            agent_authentication="",  # Add token if required
            agent_name=settings.SKYWALKING_SERVICE_NAME,
            agent_instance_name=settings.SKYWALKING_INSTANCE,
            agent_log_reporter_active=True,
            agent_meter_reporter_active=True,
        )

        agent.start()

        logger.info(
            f"SkyWalking initialized (service={settings.SKYWALKING_SERVICE_NAME}, "
            f"backend={settings.SKYWALKING_BACKEND_ADDRESS})"
        )
        return True

    except ImportError:
        logger.error(
            "apache-skywalking not installed. Run: pip install apache-skywalking"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to initialize SkyWalking: {e}")
        return False


def init_monitoring():
    """Initialize all monitoring tools."""
    init_sentry()
    init_skywalking()
