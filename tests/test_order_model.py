"""Unit tests for Order model and status transitions."""
import pytest
from app.models.order import Order, OrderStatus, can_transition, is_item_event_superseded, ORDER_STATE_TRANSITIONS, OMS_STATUS_MAP, STATUS_EVENT_MAP


class TestOrderStatus:
    """Test OrderStatus enum and transitions."""

    def test_order_status_values(self):
        """Test all order status values exist."""
        assert OrderStatus.RECEIVED.value == "received"
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.VALIDATED.value == "validated"
        assert OrderStatus.COOLING_OFF.value == "cooling_off"
        assert OrderStatus.PROCESSING.value == "processing"
        assert OrderStatus.PRINTREADY.value == "printready"
        assert OrderStatus.PRINTED.value == "printed"
        assert OrderStatus.PRODUCED.value == "produced"
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

    def test_can_transition_validated_to_processing(self):
        """Test VALIDATED -> PROCESSING transition is allowed.

        Since the PROCESSING state was introduced, VALIDATED no longer jumps
        straight to PRINTREADY — it must pass through PROCESSING first.
        """
        assert can_transition(OrderStatus.VALIDATED, OrderStatus.PROCESSING) is True
        assert can_transition(OrderStatus.VALIDATED, OrderStatus.PRINTREADY) is False

    def test_cooling_off_sits_between_validated_and_processing(self):
        """VALIDATED may enter COOLING_OFF (client cooling-off period), which
        then flows on to PROCESSING or CANCELLED."""
        assert can_transition(OrderStatus.VALIDATED, OrderStatus.COOLING_OFF) is True
        assert can_transition(OrderStatus.COOLING_OFF, OrderStatus.PROCESSING) is True
        assert can_transition(OrderStatus.COOLING_OFF, OrderStatus.CANCELLED) is True
        # Cooling-off never skips ahead to the print stages
        assert can_transition(OrderStatus.COOLING_OFF, OrderStatus.PRINTREADY) is False

    def test_cooling_off_is_internal_only(self):
        """COOLING_OFF, like PENDING/PROCESSING, is never reported to OMS/VFS
        — it must be absent from both notification vocabularies."""
        assert OrderStatus.COOLING_OFF not in OMS_STATUS_MAP
        assert OrderStatus.COOLING_OFF not in STATUS_EVENT_MAP

    def test_can_transition_printready_to_printed(self):
        """Test PRINTREADY -> PRINTED transition is allowed."""
        assert can_transition(OrderStatus.PRINTREADY, OrderStatus.PRINTED) is True

    def test_can_transition_printed_to_produced(self):
        """Test PRINTED -> PRODUCED transition is allowed (TI-65)."""
        assert can_transition(OrderStatus.PRINTED, OrderStatus.PRODUCED) is True

    def test_cannot_transition_printed_to_shipped_directly(self):
        """TI-65: PRINTED must go through PRODUCED before SHIPPED."""
        assert can_transition(OrderStatus.PRINTED, OrderStatus.SHIPPED) is False

    def test_can_transition_produced_to_shipped(self):
        """Test PRODUCED -> SHIPPED transition is allowed (TI-65)."""
        assert can_transition(OrderStatus.PRODUCED, OrderStatus.SHIPPED) is True

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


class TestItemEventSuperseded:
    """Test is_item_event_superseded (TI-65 adds PRODUCED to the terminal set)."""

    def test_second_produced_event_while_still_printed_is_superseded(self):
        """A later item's order_item_produced, while the order is already
        PRINTED (from an earlier item), is a superseded straggler."""
        assert is_item_event_superseded(OrderStatus.PRINTED, OrderStatus.PRINTED) is True

    def test_item_event_after_produced_is_superseded(self):
        """Any order_item_* event arriving after the order already reached
        PRODUCED (all items accounted for) is superseded, not invalid."""
        assert is_item_event_superseded(OrderStatus.PRODUCED, OrderStatus.RECEIVED) is True
        assert is_item_event_superseded(OrderStatus.PRODUCED, OrderStatus.PRINTREADY) is True
        assert is_item_event_superseded(OrderStatus.PRODUCED, OrderStatus.PRINTED) is True


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
        assert order.version == 1  # default version (migration f6a7b8c9d0e1)
        assert order.cooling_off_seconds is None  # no cooling-off applied yet

    def test_order_cooling_off_seconds_snapshot(self):
        """The applied cooling-off period is snapshotted on the order when it
        enters COOLING_OFF (NULL = never cooled off)."""
        order = Order(
            order_id="test-order-cool",
            source_account="test_account",
            source_order_id="src-order-cool",
            status=OrderStatus.COOLING_OFF,
            cooling_off_seconds=3600,
        )
        assert order.cooling_off_seconds == 3600

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
