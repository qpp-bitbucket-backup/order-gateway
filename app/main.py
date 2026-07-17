from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import copy
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
from app.api import orders, products, files, clients, sync, webhooks

# Global logging configuration
# Configure root logger so all modules (including app.api.orders) inherit uvicorn-style console output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
# Suppress SQLAlchemy engine and pool logs
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Suppress SQLAlchemy logs after uvicorn logging setup
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

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

## Support
For API support, contact: itdev2@qpp.com
    """,
    docs_url="/docs",
    redoc_url=None,
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
            "name": "Products Admin",
            "description": "Product/SKU management operations (requires x-admin-key)",
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
app.include_router(products.admin_router)
app.include_router(files.router)
app.include_router(clients.router)
app.include_router(sync.router)
app.include_router(webhooks.router)


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
        for method, operation in path_item.items():
            if not path.startswith("/api"):
                continue
            if method not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                continue
            operation["security"] = ONEFLOW_SECURITY

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


def _get_filtered_openapi():
    """Generate a filtered OpenAPI schema for ReDoc (Orders + File Upload only)."""
    schema = copy.deepcopy(custom_openapi())
    allowed_tags = {"Orders", "File Upload"}
    filtered_paths = {}
    for path, path_item in schema.get("paths", {}).items():
        filtered_ops = {}
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                continue
            op_tags = set(operation.get("tags", []))
            if op_tags & allowed_tags:
                filtered_ops[method] = operation
        if filtered_ops:
            filtered_paths[path] = filtered_ops
    schema["paths"] = filtered_paths
    schema["tags"] = [t for t in schema.get("tags", []) if t["name"] in allowed_tags]
    return schema


@app.get("/openapi_redoc.json", include_in_schema=False)
def openapi_redoc():
    """Filtered OpenAPI schema for ReDoc."""
    return _get_filtered_openapi()


@app.get("/redoc", include_in_schema=False, response_class=HTMLResponse)
def redoc():
    """ReDoc documentation - shows only Orders and File Upload APIs."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{settings.APP_NAME} - ReDoc</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
        <style>body {{ margin: 0; padding: 0; }}</style>
    </head>
    <body>
        <redoc spec-url='/openapi_redoc.json'></redoc>
        <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
    </body>
    </html>
    """


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint - Monitors database and RabbitMQ connectivity."""
    health_status = get_full_health_status()
    
    status_code = 200 if health_status["status"] == "healthy" else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": health_status["status"],
            "timestamp": health_status["timestamp"],
            "components": health_status["components"],
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
