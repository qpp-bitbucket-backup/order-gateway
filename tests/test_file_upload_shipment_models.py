"""Unit tests for the FileUpload and OrderShipment models."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.file_upload import FileUpload
from app.models.shipment import OrderShipment


class TestFileUpload:
    def test_required_fields(self):
        upload = FileUpload(
            file_id="f-1", mime_type="image/png",
            upload_url="https://s3/upload", fetch_url="https://cdn/fetch",
        )
        assert upload.file_id == "f-1"

    def test_status_defaults_pending(self):
        upload = FileUpload(
            file_id="f-2", mime_type="application/pdf",
            upload_url="https://s3/upload", fetch_url="https://cdn/fetch",
        )
        assert upload.status == "pending"

    def test_optional_fields(self):
        upload = FileUpload(
            file_id="f-3", mime_type="image/jpeg",
            upload_url="https://s3/upload", fetch_url="https://cdn/fetch",
        )
        assert upload.uploaded_at is None
        assert upload.file_size is None
        assert upload.store_id is None

    def test_file_size_constraint_not_enforced_at_construction(self):
        # SQLModel table=True models skip pydantic validation on __init__;
        # ge/nullable constraints are enforced by the DB schema instead.
        upload = FileUpload(
            file_id="f-4", mime_type="image/png",
            upload_url="https://s3/upload", fetch_url="https://cdn/fetch",
            file_size=-1,
        )
        assert upload.file_size == -1

    def test_inherits_is_active_default(self):
        upload = FileUpload(
            file_id="f-5", mime_type="image/png",
            upload_url="https://s3/upload", fetch_url="https://cdn/fetch",
        )
        assert upload.is_active is True

    def test_tablename(self):
        assert FileUpload.__tablename__ == "file_uploads"


class TestOrderShipment:
    def test_construction_without_order_id_allowed(self):
        # table=True models skip validation; required-ness lives in the DB
        # schema (nullable=False), not the Python constructor.
        assert OrderShipment().order_id is None

    def test_minimal(self):
        shipment = OrderShipment(order_id="ord-1")
        assert shipment.store_order_id is None
        assert shipment.qpmn_shipment_id is None
        assert shipment.items is None

    def test_full_payload(self):
        shipped_at = datetime.now(timezone.utc)
        shipment = OrderShipment(
            order_id="ord-2",
            store_order_id="qpmn-88",
            qpmn_shipment_id="ship-1",
            shipment_index=0,
            tracking_number="TRK999",
            tracking_url="https://t.example.com/TRK999",
            carrier="FedEx",
            ship_date=shipped_at,
            items=[{"itemId": "item-6", "quantity": 3}],
        )
        assert shipment.carrier == "FedEx"
        assert shipment.items[0]["quantity"] == 3

    def test_tablename(self):
        assert OrderShipment.__tablename__ == "order_shipments"
