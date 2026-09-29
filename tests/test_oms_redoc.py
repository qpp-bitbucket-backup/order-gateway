"""Unit tests for the OMS ReDoc page and its filtered OpenAPI schema.

These endpoints are plain doc routes (no DB / auth involved); the TestClient
only needs create_db_and_tables stubbed out so the lifespan stays DB-free.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(name="client")
def client_fixture():
    with patch("app.main.create_db_and_tables", lambda: None), TestClient(app) as client:
        yield client


class TestOpenAPIOMSSchema:
    def test_contains_only_the_two_oms_endpoints(self, client):
        resp = client.get("/openapi_oms.json")
        assert resp.status_code == 200
        schema = resp.json()

        assert set(schema["paths"].keys()) == {
            "/api/oms/order/{source_order_id}/cancel",
            "/api/oms/order/{source_order_id}/return",
        }
        # Both endpoints are POST (the cancel endpoint was migrated off PUT).
        assert set(schema["paths"]["/api/oms/order/{source_order_id}/cancel"].keys()) == {"post"}
        assert set(schema["paths"]["/api/oms/order/{source_order_id}/return"].keys()) == {"post"}

        # One "Order" section; the two entries render as "POST Cancel" /
        # "POST Return" (method badge + operation summary).
        assert [t["name"] for t in schema["tags"]] == ["Order"]
        cancel_op = schema["paths"]["/api/oms/order/{source_order_id}/cancel"]["post"]
        return_op = schema["paths"]["/api/oms/order/{source_order_id}/return"]["post"]
        assert cancel_op["tags"] == ["Order"]
        assert cancel_op["summary"] == "Cancel"
        assert return_op["tags"] == ["Order"]
        assert return_op["summary"] == "Return"
        assert "x-tagGroups" not in schema

    def test_info_carries_oms_specific_title_and_auth_notes(self, client):
        schema = client.get("/openapi_oms.json").json()

        assert schema["info"]["title"].endswith("OMS API")
        description = schema["info"]["description"]
        # Per-client OMS credentials from the clients table, managed via the
        # platform API — not the VFS/OneFlow client tokens.
        assert "oms_token" in description
        assert "oms_secret" in description
        assert "/api/platform/clients/oms_secret" in description
        assert "POST /api/oms/order/ORD-123/cancel" in description

    def test_return_request_schema_keeps_simplified_fields(self, client):
        schema = client.get("/openapi_oms.json").json()
        operation = schema["paths"]["/api/oms/order/{source_order_id}/return"]["post"]
        ref_name = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        model_name = ref_name.split("/")[-1]
        assert model_name == "OMSOrderReturnRequest"

        properties = schema["components"]["schemas"][model_name]["properties"]
        # Simplified logistics fields (no "return" prefix) + partial-return lines.
        assert "trackingNumber" in properties
        assert "carrierName" in properties
        assert "trackingUrl" in properties
        assert "items" in properties
        assert "returnTrackingNumber" not in properties
        assert "carrier" not in properties

        # Optional[List[OMSReturnItem]] is emitted as
        # anyOf: [{items: {$ref}, type: array}, {type: null}].
        items_prop = properties["items"]
        array_option = next(
            option for option in items_prop.get("anyOf", []) if option.get("type") == "array"
        )
        assert array_option["items"]["$ref"].endswith("OMSReturnItem")


class TestOMSReDocPage:
    def test_serves_redoc_html_pointing_at_oms_schema(self, client):
        resp = client.get("/oms-api-doc")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "spec-url='/openapi_oms.json'" in resp.text
        assert "redoc.standalone.js" in resp.text


class TestExistingReDocUnaffected:
    """The refactored _get_filtered_openapi() must not change /redoc."""

    def test_redoc_schema_excludes_oms(self, client):
        schema = client.get("/openapi_redoc.json").json()
        assert schema["paths"], "Orders/File Upload endpoints disappeared"
        assert not any(path.startswith("/api/oms") for path in schema["paths"])
        assert not any(
            t["name"] in {"OMS", "Order"}
            for t in schema["tags"]
        )
        assert not schema["info"]["title"].endswith("OMS API")

    def test_redoc_page_still_serves(self, client):
        resp = client.get("/redoc")
        assert resp.status_code == 200
        assert "spec-url='/openapi_redoc.json'" in resp.text
