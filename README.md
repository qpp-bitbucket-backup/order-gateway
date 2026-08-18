# HP PrintOS Site Flow API Gateway

A FastAPI-based API gateway that implements the HP PrintOS Site Flow API specification for managing print orders, including validation, submission, and status tracking.

## Features

- **Order Validation**: Validate orders before submission without creating them
- **Order Submission**: Submit new print orders to the system
- **Order Management**: Retrieve, list, and cancel orders
- **File Upload**: Get pre-signed URLs for file uploads
- **Product Catalog**: Access available products and SKUs
- **MySQL Database**: Persistent storage using SQLModel ORM
- **OpenAPI Documentation**: Interactive API docs at `/docs` and `/redoc`

## API Endpoints

### Orders
- `POST /api/order/validate` - Validate an order
- `POST /api/order` - Submit a new order
- `GET /api/order` - List all orders (paginated)
- `GET /api/order/{orderId}` - Get order details by ID
- `PUT /api/order/{sourceAccount}/{sourceOrderId}/cancel` - Cancel an order

### Products
- `GET /api/product` - Get list of available products
- `GET /api/sku` - Get list of available SKUs

### Files
- `GET /api/file/getpreupload` - Get pre-signed upload URLs

## Quick Start

### Using Docker Compose (Recommended)

1. Clone the repository
2. Run with Docker Compose:
```bash
docker-compose up -d
```

3. Access the API at `http://localhost:8000`
4. View API documentation at `http://localhost:8000/docs`

### Manual Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file from the example:
```bash
cp .env.example .env
```

3. Update the `.env` file with your MySQL credentials

4. Run the application:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| DATABASE_URL | MySQL connection string | mysql+pymysql://root:password@localhost:3306/order_gateway |
| APP_NAME | Application name | Order Gateway API |
| APP_VERSION | Application version | 1.0.0 |
| DEBUG | Enable debug mode | true |
| HOST | Server host | 0.0.0.0 |
| PORT | Server port | 8000 |
| SECRET_KEY | JWT secret key | change-in-production |
| ALLOWED_ORIGINS | CORS allowed origins | ["http://localhost:3000"] |

## Project Structure

```
order-gateway/
├── app/
│   ├── api/              # API route handlers
│   │   ├── orders.py     # Order management endpoints
│   │   ├── products.py   # Product and SKU endpoints
│   │   └── files.py      # File upload endpoints
│   ├── core/             # Core configuration and utilities
│   │   ├── config.py     # Application settings
│   │   ├── database.py   # Database connection
│   │   ├── security.py   # Authentication utilities
│   │   └── middleware.py # Custom middleware
│   ├── models/           # SQLModel database models
│   │   ├── base.py       # Base model
│   │   ├── order.py      # Order models
│   │   ├── product.py    # Product and SKU models
│   │   └── file_upload.py # File upload model
│   ├── schemas/          # Pydantic schemas
│   │   ├── order.py      # Order request/response schemas
│   │   ├── product.py    # Product schemas
│   │   └── file_upload.py # File upload schemas
│   └── main.py           # Application entry point
├── requirements.txt      # Python dependencies
├── Dockerfile            # Docker build configuration
├── docker-compose.yml    # Docker Compose configuration
├── .env.example          # Example environment variables
└── swagger.json          # Original API specification
```

## Database Schema

The application uses the following main tables:

- **orders**: Stores print orders with complete order data
- **products**: Available print products
- **skus**: Stock keeping units linked to products
- **file_uploads**: Tracks file upload sessions

## Testing

Test the API using the interactive documentation:

1. Open `http://localhost:8000/docs` in your browser
2. Try out each endpoint directly from the Swagger UI
3. Use the "Try it out" button to execute requests

Example curl commands:

```bash
# Health check
curl http://localhost:8000/health

# Validate an order
curl -X POST http://localhost:8000/api/order/validate \
  -H "Content-Type: application/json" \
  -d '{
    "destination": {"name": "test_account"},
    "orderData": {
      "sourceOrderId": "ORDER-001",
      "items": [
        {
          "sku": "BUSINESS-CARD",
          "components": [
            {
              "code": "Content",
              "fetch": true,
              "path": "https://example.com/card.pdf"
            }
          ]
        }
      ]
    }
  }'

# Get all orders
curl http://localhost:8000/api/order?page=1&pagesize=10

# Get products
curl http://localhost:8000/api/product

# Get SKUs
curl http://localhost:8000/api/sku
```

## Verify Order Worker
celery -A app.core.celery.celery_app worker --loglevel=info --pool=solo

## Production Deployment

For production deployment:

1. Set `DEBUG=false` in environment variables
2. Change the `SECRET_KEY` to a strong random value
3. Configure proper database credentials
4. Set up SSL/TLS termination
5. Configure CORS `ALLOWED_ORIGINS` appropriately
6. Use a process manager like Gunicorn:

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## Technology Stack

- **FastAPI**: Modern, fast web framework for building APIs
- **SQLModel**: SQL database ORM with Pydantic integration
- **MySQL**: Relational database
- **Pydantic**: Data validation using Python type annotations
- **Uvicorn**: ASGI server
- **Docker**: Containerization

## License

This project is provided as-is for integration with HP PrintOS Site Flow.
