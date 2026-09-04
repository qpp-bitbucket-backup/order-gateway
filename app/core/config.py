from pydantic_settings import BaseSettings
from typing import List, Optional
from pathlib import Path
import tomllib


def _get_version() -> str:
    """Read version dynamically from pyproject.toml."""
    pyproject_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
    except (FileNotFoundError, KeyError):
        return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Order Gateway API"
    APP_VERSION: str = _get_version()
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database (Split Configuration)
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USERNAME: str = "order_user"
    DB_PASSWORD: str = "order_password"
    DB_NAME: str = "order_gateway"

    # Security & Authentication
    SECRET_KEY: str = "your-secret-key-here-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # HP Site Flow OneFlow API Authentication (bootstrap default client)
    ONEFLOW_TOKEN: str = "oneflow-token-change-in-production"
    ONEFLOW_SECRET: str = "oneflow-secret-change-in-production"

    # Admin API key for client management endpoints
    ADMIN_API_KEY: str = "admin-api-key-change-in-production"

    # Default Admin User (seeded on first startup)
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_EMAIL: str = "admin@order-gateway.com"
    DEFAULT_ADMIN_PASSWORD: str = "admin123"

    # HP PrintOS webhook credentials (for inbound webhooks)
    HP_CLIENT_ID: str = "hp-client-id-here"
    HP_CLIENT_SECRET: str = "hp-client-secret-here"

    # OMS HUB4 transport configuration (Baozun standard)
    OMS_APP_SECRET: str = ""
    OMS_SOURCE_APP: str = ""
    OMS_INTERFACE_TYPE: str = "1"
    OMS_STATUS_METHOD_NAME: str = "shipment_order_notify"

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Celery Configuration (RabbitMQ)
    CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:5672//"
    CELERY_RESULT_BACKEND: str = "rpc://"

    # Celery Beat - Product Sync Schedule Configuration
    CELERY_BEAT_SYNC_ENABLED: bool = True
    CELERY_BEAT_SYNC_PRODUCT_INTERVAL_MINUTES: int = 5  # Sync products every N minutes

    # Celery Beat - Daily Sales Stats Schedule Configuration
    CELERY_BEAT_SALES_STATS_ENABLED: bool = True
    # UTC hour to run the daily sales stats aggregation. The Celery app runs in
    # UTC, so 17 = 01:00 Asia/Hong_Kong (UTC+8, no DST) — i.e. 1 AM HK time daily.
    CELERY_BEAT_SALES_STATS_HOUR: int = 17

    # QPMN API Configuration
    QPMN_API_URL: str = "https://stage.qpmarketnetwork.com/cgp-rest/api"
    QPMN_API_KEY: str = ""
    QPMN_OPEN_API_URL: str = "https://test-qpmn.qppdev.com/stage/cgp-rest/open-api/v1/"
    QPMN_PUSH_RETRY_COUNT: int = 5        # Max retry attempts when QPMN returns 503/timeout
    QPMN_PUSH_RETRY_COUNTDOWN: int = 900   # Base delay in seconds for exponential backoff
    QPMN_PUSH_RETRY_MAX_COUNTDOWN: int = 7200  # Max delay cap in seconds (2 hours)
    QPMN_PAYMENT_METHOD: str = "PayPal"      # Default payment method for QPMN orders
    QPMN_ORDER_API_VERSION: str = "legacy"   # Order creation API version: "legacy" or "open"
    # Fallback packaging limit when a SKU has no max_package_quantity set.
    # In practice skus.max_package_quantity is NOT NULL with a DB default of
    # 50, so this only matters as a defensive fallback.
    PACKAGE_MAX_QUANTITY_DEFAULT: int = 50

    # PDF Processing Configuration
    MODIFY_PDF_RESOLUTION: bool = False
    PDF_TARGET_WIDTH: float = 595.0  # A4 width in points (8.27 inches)
    PDF_TARGET_HEIGHT: float = 842.0  # A4 height in points (11.69 inches)
    # QPMN rejects design PDFs that are not a PDF/X standard
    # (PDF/X-1a:2001, PDF/X-1a:2003, PDF/X-3:2002, PDF/X-3:2003, PDF/X-4:2008).
    # Use PDF/X-4:2008 for RGB image content; X-1a flavors forbid RGB.
    PDF_CONVERT_TO_PDFX: bool = True
    PDF_X_STANDARD: str = "PDF/X-4:2008"
    # Render the split single-page design PDFs to PNG before QPMN upload
    # (parallel-card files are always watermarked to PNG regardless of this)
    CONVERT_TO_PNG: bool = False
    PDF_TO_PNG_DPI: int = 300

    # OMS API Configuration
    OMS_API_URL: str = ""
    OMS_VALIDATE_RETRY_COUNT: int = 5       # Max retry attempts when OMS returns 503/timeout
    OMS_VALIDATE_RETRY_COUNTDOWN: int = 300  # Base delay in seconds for exponential backoff
    OMS_VALIDATE_RETRY_MAX_COUNTDOWN: int = 3600  # Max delay cap in seconds (1 hour)
    OMS_NOTIFY_RETRY_COUNT: int = 5         # Max retry attempts when OMS webhook returns 503/timeout
    OMS_NOTIFY_RETRY_COUNTDOWN: int = 300    # Base delay in seconds for exponential backoff
    OMS_NOTIFY_RETRY_MAX_COUNTDOWN: int = 3600  # Max delay cap in seconds (1 hour)
    VFS_NOTIFY_RETRY_COUNT: int = 5         # Max retry attempts when VFS postback returns 503/timeout
    VFS_NOTIFY_RETRY_COUNTDOWN: int = 300    # Base delay in seconds for exponential backoff
    VFS_NOTIFY_RETRY_MAX_COUNTDOWN: int = 3600  # Max delay cap in seconds (1 hour)

    # Alibaba Cloud OSS Configuration
    OSS_ACCESS_KEY_ID: str = "your-oss-access-key-id"
    OSS_ACCESS_KEY_SECRET: str = "your-oss-access-key-secret"
    OSS_ENDPOINT: str = "oss-cn-hangzhou.aliyuncs.com"  # e.g., oss-cn-hangzhou.aliyuncs.com
    OSS_BUCKET_NAME: str = "your-bucket-name"
    OSS_REGION: str = "cn-hangzhou"  # e.g., cn-hangzhou
    OSS_UPLOAD_EXPIRY_SECONDS: int = 3600  # Pre-signed URL expiry time (1 hour)
    OSS_DOWNLOAD_EXPIRY_SECONDS: int = 86400  # Download URL expiry time (24 hours)

    # Sentry Configuration
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0
    SENTRY_ENVIRONMENT: str = "development"

    # SkyWalking Configuration
    SKYWALKING_ENABLED: bool = False
    SKYWALKING_SERVICE_NAME: str = "order-gateway"
    SKYWALKING_INSTANCE: str = "localhost"
    SKYWALKING_BACKEND_ADDRESS: str = "localhost:11800"

    @property
    def DATABASE_URL(self) -> str:
        """Construct database URL from individual components."""
        return f"mysql+pymysql://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
