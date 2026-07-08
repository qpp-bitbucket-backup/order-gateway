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

    # HP PrintOS webhook credentials (for inbound webhooks)
    HP_CLIENT_ID: str = "hp-client-id-here"
    HP_CLIENT_SECRET: str = "hp-client-secret-here"

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Celery Configuration (RabbitMQ)
    CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:5672//"
    CELERY_RESULT_BACKEND: str = "rpc://"

    # QPMN API Configuration
    QPMN_API_URL: str = "https://api.qpmn.com/v1"
    QPMN_API_KEY: str = ""

    # OMS API Configuration
    OMS_API_URL: str = ""

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
        return f"mysql+pymysql://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
