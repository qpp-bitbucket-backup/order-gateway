"""Unit tests for configuration settings."""
import pytest
from app.core.config import settings, Settings


class TestSettings:
    """Test application settings."""

    def test_settings_instance_exists(self):
        """Test that settings instance is created."""
        assert settings is not None

    def test_default_app_name(self):
        """Test default app name."""
        assert settings.APP_NAME == "Order Gateway API"

    def test_default_debug_mode(self):
        """Test default debug mode."""
        assert settings.DEBUG is True

    def test_default_host(self):
        """Test default host."""
        assert settings.HOST == "0.0.0.0"

    def test_default_port(self):
        """Test default port."""
        assert settings.PORT == 8000

    def test_default_celery_broker_url(self):
        """Test default Celery broker URL."""
        assert "amqp://" in settings.CELERY_BROKER_URL

    def test_celery_beat_sync_enabled_default(self):
        """Test Celery Beat sync enabled default."""
        assert settings.CELERY_BEAT_SYNC_ENABLED is True

    def test_celery_beat_sync_interval_configured(self):
        """Test Celery Beat sync interval is configured."""
        # Value may be overridden by .env file
        assert isinstance(settings.CELERY_BEAT_SYNC_PRODUCT_INTERVAL_MINUTES, int)
        assert settings.CELERY_BEAT_SYNC_PRODUCT_INTERVAL_MINUTES > 0

    def test_modify_pdf_resolution_configured(self):
        """Test PDF resolution modification is configured."""
        # Value may be overridden by .env file
        assert isinstance(settings.MODIFY_PDF_RESOLUTION, bool)

    def test_pdf_target_dimensions_configured(self):
        """Test PDF target dimensions are configured."""
        # Values may be overridden by .env file
        assert isinstance(settings.PDF_TARGET_WIDTH, float)
        assert isinstance(settings.PDF_TARGET_HEIGHT, float)
        assert settings.PDF_TARGET_WIDTH > 0
        assert settings.PDF_TARGET_HEIGHT > 0

    def test_database_url_construction(self):
        """Test database URL is constructed correctly."""
        db_url = settings.DATABASE_URL
        assert "mysql+pymysql://" in db_url
        assert settings.DB_USERNAME in db_url
        assert settings.DB_NAME in db_url

    def test_cors_origins_default(self):
        """Test default CORS origins."""
        assert "http://localhost:3000" in settings.ALLOWED_ORIGINS
        assert "http://localhost:8080" in settings.ALLOWED_ORIGINS
