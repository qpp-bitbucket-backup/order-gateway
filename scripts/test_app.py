"""
Test script to verify the Order Gateway API is working correctly.
"""
import os
import subprocess
import time
import requests
import json

from app.core.auth_oneflow import build_auth_headers
from app.core.config import settings


def auth_headers(method: str, path: str) -> dict[str, str]:
    """Build OneFlow auth headers for test requests."""
    return build_auth_headers(
        settings.ONEFLOW_TOKEN,
        settings.ONEFLOW_SECRET,
        method,
        path,
    )


def wait_for_server(url, timeout=10):
    """Wait for the server to start."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{url}/health")
            if response.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.5)
    return False


def test_api():
    """Test all API endpoints."""
    base_url = "http://localhost:8000"

    print("=" * 60)
    print("HP PrintOS Site Flow API Gateway - Test Suite")
    print("=" * 60)

    # Wait for server to start
    print("\n[1/7] Waiting for server to start...")
    if not wait_for_server(base_url):
        print("FAILED: Server failed to start")
        return False
    print("OK: Server is running")

    # Test health endpoint
    print("\n[2/7] Testing health endpoint...")
    response = requests.get(f"{base_url}/health")
    assert response.status_code == 200, f"Health check failed: {response.status_code}"
    print(f"OK: Health check passed: {response.json()}")

    # Test order validation
    print("\n[3/7] Testing order validation...")
    validation_data = {
        "destination": {"name": "test_account"},
        "orderData": {
            "sourceOrderId": "TEST-ORDER-001",
            "items": [
                {
                    "sku": "BUSINESS-CARD",
                    "components": [
                        {
                            "code": "Content",
                            "fetch": True,
                            "path": "https://example.com/card.pdf"
                        }
                    ]
                }
            ],
            "shipments": [
                {
                    "shipTo": {
                        "name": "John Doe",
                        "address1": "123 Main St",
                        "town": "Springfield",
                        "postcode": "62701",
                        "isoCountry": "US"
                    },
                    "carrier": {
                        "code": "fedex",
                        "service": "ground"
                    }
                }
            ]
        }
    }
    response = requests.post(
        f"{base_url}/api/order/validate",
        json=validation_data,
        headers=auth_headers("POST", "/api/order/validate"),
    )
    assert response.status_code == 200, f"Order validation failed: {response.status_code}"
    result = response.json()
    print(f"OK: Order validation passed: success={result['success']}")

    # Test get products
    print("\n[4/7] Testing get products...")
    response = requests.get(
        f"{base_url}/api/product",
        headers=auth_headers("GET", "/api/product"),
    )
    assert response.status_code == 200, f"Get products failed: {response.status_code}"
    result = response.json()
    print(f"OK: Get products passed: count={result['count']}, success={result['success']}")

    # Test get SKUs
    print("\n[5/7] Testing get SKUs...")
    response = requests.get(
        f"{base_url}/api/sku",
        headers=auth_headers("GET", "/api/sku"),
    )
    assert response.status_code == 200, f"Get SKUs failed: {response.status_code}"
    result = response.json()
    print(f"OK: Get SKUs passed: count={result['count']}, success={result['success']}")

    # Test file upload URLs
    print("\n[6/7] Testing file upload URLs...")
    response = requests.get(
        f"{base_url}/api/file/getpreupload",
        params={"mime_type": "application/pdf"},
        headers=auth_headers("GET", "/api/file/getpreupload"),
    )
    assert response.status_code == 200, f"Get upload URLs failed: {response.status_code}"
    result = response.json()
    print(f"OK: File upload URLs generated: upload URL present={bool(result['upload'])}")

    # Test get orders (should be empty initially)
    print("\n[7/7] Testing get all orders...")
    response = requests.get(
        f"{base_url}/api/order",
        params={"page": 1, "pagesize": 10},
        headers=auth_headers("GET", "/api/order"),
    )
    assert response.status_code == 200, f"Get orders failed: {response.status_code}"
    result = response.json()
    print(f"OK: Get orders passed: count={result['count']}, pages={result['pages']}")

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)
    print("\nAPI Documentation available at:")
    print(f"  - Swagger UI: {base_url}/docs")
    print(f"  - ReDoc: {base_url}/redoc")
    print(f"  - OpenAPI JSON: {base_url}/openapi.json")
    return True


if __name__ == "__main__":
    try:
        test_api()
    except Exception as e:
        print(f"\nFAILED: Test failed with error: {e}")
        import traceback
        traceback.print_exc()
