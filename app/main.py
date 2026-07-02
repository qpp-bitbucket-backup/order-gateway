from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.database import create_db_and_tables
from app.core.monitoring import init_monitoring
from app.core.health import get_full_health_status
from app.core.auth_oneflow import (
    oneflow_algorithm_header,
    oneflow_authorization_header,
    oneflow_date_header,
)
from app.api import orders, products, files, clients, sync

# Global logging configuration
# Suppress SQLAlchemy engine and pool logs
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: Initialize monitoring (Sentry + SkyWalking)
    init_monitoring()
    
    # Create database tables
    create_db_and_tables()
    yield
    # Shutdown: Clean up resources if needed
    pass


# Create FastAPI application with security documentation
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
QPMN Order Gateway API - Manages print orders, including validation, submission, and status tracking.

## Authentication

All API endpoints require OneFlow signed request headers.

### Required Headers
- `x-oneflow-authorization`: `Token:Signature`
- `x-oneflow-date`: ISO 8601 timestamp used when signing (e.g. `2026-01-01T12:00:00.000Z`)
- `x-oneflow-algorithm`: `SHA256`

### Signature
Sign the following string with HMAC-SHA256 using your Secret:
```
{HTTP_METHOD} {PATH} {TIMESTAMP}
```
Example:
```
GET /api/order 2026-01-01T12:00:00.000Z
```

### Getting Credentials
Contact QPMN support to obtain your Token and Secret.

### Using Swagger UI
1. Click **Authorize** and fill in all three headers.
2. `x-oneflow-date` must match the timestamp used to compute the signature.
3. Recompute `x-oneflow-authorization` for each endpoint because the signature includes HTTP method and path.

## Rate Limiting
- 100 requests per minute for standard accounts
- 1000 requests per minute for premium accounts

## Support
For API support, contact: itdev2@qpp.com
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Orders",
            "description": "Order management operations including validation, submission, and tracking",
        },
        {
            "name": "Products",
            "description": "Product and SKU catalog operations",
        },
        {
            "name": "File Upload",
            "description": "File upload URL generation for print assets",
        },
        {
            "name": "Clients",
            "description": "API client credential management (requires x-admin-key)",
        },
        {
            "name": "Product Sync",
            "description": "Product synchronization from QPMN API (requires x-admin-key)",
        },
        {
            "name": "Health",
            "description": "Health check and status endpoints (no authentication required)",
        },
    ],
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(orders.router)
app.include_router(products.router)
app.include_router(files.router)
app.include_router(clients.router)
app.include_router(sync.router)


ONEFLOW_SECURITY = [
    {
        "OneFlowAuthorization": [],
        "OneFlowDate": [],
        "OneFlowAlgorithm": [],
    }
]


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    openapi_schema["components"]["securitySchemes"] = {
        "OneFlowAuthorization": {
            "type": "apiKey",
            "in": "header",
            "name": "x-oneflow-authorization",
            "description": oneflow_authorization_header.model.description,
        },
        "OneFlowDate": {
            "type": "apiKey",
            "in": "header",
            "name": "x-oneflow-date",
            "description": oneflow_date_header.model.description,
        },
        "OneFlowAlgorithm": {
            "type": "apiKey",
            "in": "header",
            "name": "x-oneflow-algorithm",
            "description": oneflow_algorithm_header.model.description,
        },
    }

    for path, path_item in openapi_schema.get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                continue
            operation["security"] = ONEFLOW_SECURITY

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/", tags=["Health"])
def root():
    """Root endpoint - Health check."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "authentication": "OneFlow signed headers required for all /api/* endpoints"
    }


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint - Monitors database and RabbitMQ connectivity."""
    health_status = get_full_health_status()
    
    return {
        "status": health_status["status"],
        "timestamp": health_status["timestamp"],
        "components": health_status["components"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
