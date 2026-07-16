"""Unit tests for Order model and status transitions."""
import pytest
from app.models.order import Order, OrderStatus, can_transition, ORDER_STATE_TRANSITIONS


class TestOrderStatus:
    """Test OrderStatus enum and transitions."""

    def test_order_status_values(self):
        """Test all order status values exist."""
        assert OrderStatus.RECEIVED.value == "received"
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.VALIDATED.value == "validated"
        assert OrderStatus.PRINTREADY.value == "printready"
        assert OrderStatus.PRINTED.value == "printed"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.FAILED.value == "failed"
        assert OrderStatus.ERRORED.value == "errored"
        assert OrderStatus.SHIPPED.value == "shipped"

    def test_can_transition_received_to_pending(self):
        """Test RECEIVED -> PENDING transition is allowed."""
        assert can_transition(OrderStatus.RECEIVED, OrderStatus.PENDING) is True

    def test_can_transition_received_to_cancelled(self):
        """Test RECEIVED -> CANCELLED transition is allowed."""
        assert can_transition(OrderStatus.RECEIVED, OrderStatus.CANCELLED) is True

    def test_can_transition_received_to_failed(self):
        """Test RECEIVED -> FAILED transition is allowed."""
        assert can_transition(OrderStatus.RECEIVED, OrderStatus.FAILED) is True

    def test_cannot_transition_received_to_validated(self):
        """Test RECEIVED -> VALIDATED transition is NOT allowed."""
        assert can_transition(OrderStatus.RECEIVED, OrderStatus.VALIDATED) is False

    def test_can_transition_pending_to_validated(self):
        """Test PENDING -> VALIDATED transition is allowed."""
        assert can_transition(OrderStatus.PENDING, OrderStatus.VALIDATED) is True

    def test_can_transition_validated_to_printready(self):
        """Test VALIDATED -> PRINTREADY transition is allowed."""
        assert can_transition(OrderStatus.VALIDATED, OrderStatus.PRINTREADY) is True

    def test_can_transition_printready_to_printed(self):
        """Test PRINTREADY -> PRINTED transition is allowed."""
        assert can_transition(OrderStatus.PRINTREADY, OrderStatus.PRINTED) is True

    def test_can_transition_printed_to_shipped(self):
        """Test PRINTED -> SHIPPED transition is allowed."""
        assert can_transition(OrderStatus.PRINTED, OrderStatus.SHIPPED) is True

    def test_cannot_transition_shipped(self):
        """Test SHIPPED is a terminal state - no transitions allowed."""
        assert can_transition(OrderStatus.SHIPPED, OrderStatus.RECEIVED) is False
        assert can_transition(OrderStatus.SHIPPED, OrderStatus.PENDING) is False
        assert can_transition(OrderStatus.SHIPPED, OrderStatus.CANCELLED) is False

    def test_cannot_transition_cancelled(self):
        """Test CANCELLED is a terminal state - no transitions allowed."""
        assert can_transition(OrderStatus.CANCELLED, OrderStatus.RECEIVED) is False
        assert can_transition(OrderStatus.CANCELLED, OrderStatus.PENDING) is False

    def test_can_retry_from_failed(self):
        """Test FAILED can transition to PENDING or RECEIVED for retry."""
        assert can_transition(OrderStatus.FAILED, OrderStatus.PENDING) is True
        assert can_transition(OrderStatus.FAILED, OrderStatus.RECEIVED) is True

    def test_can_retry_from_errored(self):
        """Test ERRORED can transition to PENDING for retry."""
        assert can_transition(OrderStatus.ERRORED, OrderStatus.PENDING) is True


class TestOrderModel:
    """Test Order model creation and fields."""

    def test_order_creation_minimal(self):
        """Test creating an order with minimal required fields."""
        order = Order(
            order_id="test-order-001",
            source_account="test_account",
            source_order_id="src-order-001",
        )
        assert order.order_id == "test-order-001"
        assert order.source_account == "test_account"
        assert order.source_order_id == "src-order-001"
        assert order.status == OrderStatus.PENDING  # default status
        assert order.version == 0

    def test_order_creation_full(self):
        """Test creating an order with all fields."""
        order = Order(
            order_id="test-order-002",
            source_account="test_account",
            source_order_id="src-order-002",
            destination={"name": "Test Destination"},
            source={"name": "Test Source"},
            order_data={"items": []},
            status=OrderStatus.RECEIVED,
            store_id="store-001",
            store_order_id="store-order-001",
        )
        assert order.order_id == "test-order-002"
        assert order.destination == {"name": "Test Destination"}
        assert order.source == {"name": "Test Source"}
        assert order.status == OrderStatus.RECEIVED
        assert order.store_id == "store-001"
        assert order.store_order_id == "store-order-001"

    def test_order_logs_default_none(self):
        """Test that order logs default to None."""
        order = Order(
            order_id="test-order-003",
            source_account="test_account",
            source_order_id="src-order-003",
        )
        assert order.logs is None

    def test_order_files_default_none(self):
        """Test that order files default to None."""
        order = Order(
            order_id="test-order-004",
            source_account="test_account",
            source_order_id="src-order-004",
        )
        assert order.files is None
