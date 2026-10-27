"""Unit tests for pure helpers and status sets in app/services/order.py."""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.order import ORDER_STATE_TRANSITIONS, Order, OrderStatus, can_transition
from app.services.order import (
    DELETABLE_STATUSES,
    NON_CANCELLABLE_STATUSES,
    REPUBLISHABLE_STATUSES,
    SHIPMENT_CANCELLABLE_STATUSES,
    UPDATABLE_STATUSES,
    _build_qpmn_return_payload,
    order_service,
    parse_parallel_card_id,
)


class TestParseParallelCardId:
    """Test parallel-card sourceOrderId parsing (``aaaaaaa[-_]b_Sccccc``)."""

    def test_dash_separator(self):
        assert parse_parallel_card_id("CN-TEST20260811-1_S1055552") == (
            "CN-TEST20260811",
            "1",
            "1055552",
        )

    def test_underscore_separator(self):
        assert parse_parallel_card_id("IVAN-TEST-0004_1_S10098923") == (
            "IVAN-TEST-0004",
            "1",
            "10098923",
        )

    def test_multi_digit_version(self):
        assert parse_parallel_card_id("base-12_S77") == ("base", "12", "77")

    def test_plain_order_id_is_not_parallel(self):
        assert parse_parallel_card_id("CN-TEST20260811") is None

    def test_missing_shipping_number_rejected(self):
        assert parse_parallel_card_id("base-1_S") is None

    def test_no_s_marker_rejected(self):
        assert parse_parallel_card_id("base-1X123") is None

    def test_no_version_digits_rejected(self):
        # "-v1_S123": the version part must be digits only.
        assert parse_parallel_card_id("base-v1_S123") is None


class TestStatusSets:
    """The API guard sets must match the lifecycle rules they enforce."""

    def test_updatable_statuses_pre_print_ready(self):
        # Orders may only be edited before QPMN accepts them for printing.
        assert UPDATABLE_STATUSES == {
            OrderStatus.RECEIVED,
            OrderStatus.PENDING,
            OrderStatus.VALIDATED,
            OrderStatus.COOLING_OFF,
            OrderStatus.PROCESSING,
            OrderStatus.FAILED,
            OrderStatus.ERRORED,
        }
        assert OrderStatus.PRINTREADY not in UPDATABLE_STATUSES
        assert OrderStatus.SHIPPED not in UPDATABLE_STATUSES

    def test_deletable_statuses_are_terminal_failures(self):
        assert DELETABLE_STATUSES == {OrderStatus.FAILED, OrderStatus.CANCELLED}

    def test_republishable_statuses(self):
        assert REPUBLISHABLE_STATUSES == {OrderStatus.RECEIVED, OrderStatus.FAILED}


class TestStateMachineConsistency:
    """can_transition must reflect ORDER_STATE_TRANSITIONS exactly."""

    def test_terminal_states_allow_nothing(self):
        # CANCELLED and RETURN_COMPLETED are the true terminals. SHIPPED is
        # no longer one — it may still flow into the return path (RETURNING).
        for terminal in (OrderStatus.CANCELLED, OrderStatus.RETURN_COMPLETED):
            assert ORDER_STATE_TRANSITIONS[terminal] == []

    def test_shipped_flows_into_return_path(self):
        assert ORDER_STATE_TRANSITIONS[OrderStatus.SHIPPED] == [OrderStatus.RETURNING]
        assert ORDER_STATE_TRANSITIONS[OrderStatus.RETURNING] == [OrderStatus.RETURN_COMPLETED]

    def test_can_transition_matches_table(self):
        for from_status, allowed in ORDER_STATE_TRANSITIONS.items():
            for to_status in OrderStatus:
                assert can_transition(from_status, to_status) == (to_status in allowed)

    def test_every_status_has_entry(self):
        assert set(ORDER_STATE_TRANSITIONS) == set(OrderStatus)


class TestShipmentCancellableStatuses:
    """TI-71/72: cancellation after 'reviewed' but before 'shipped' must
    route through QPMN's shipment-cancel API (§3.4), not the order-cancel
    API (§3.3, only valid before 'reviewed')."""

    def test_shipment_cancellable_statuses(self):
        assert SHIPMENT_CANCELLABLE_STATUSES == {
            OrderStatus.PRINTREADY,
            OrderStatus.PRINTED,
            OrderStatus.PRODUCED,
        }

    def test_non_cancellable_statuses_no_longer_include_shipment_range(self):
        assert NON_CANCELLABLE_STATUSES == {
            OrderStatus.SHIPPED,
            OrderStatus.CANCELLED,
            OrderStatus.RETURNING,
            OrderStatus.RETURN_COMPLETED,
        }
        assert SHIPMENT_CANCELLABLE_STATUSES.isdisjoint(NON_CANCELLABLE_STATUSES)

    def test_shipment_range_can_transition_to_cancelled(self):
        for status in SHIPMENT_CANCELLABLE_STATUSES:
            assert can_transition(status, OrderStatus.CANCELLED)

    @staticmethod
    def _sqlite_session():
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        return Session(engine)

    @pytest.mark.parametrize("status", [OrderStatus.PRINTREADY, OrderStatus.PRINTED, OrderStatus.PRODUCED])
    def test_cancel_order_uses_shipment_cancel_api_in_shipment_range(self, status):
        session = self._sqlite_session()
        order = Order(order_id="o1", source_account="a", source_order_id="s1", status=status, store_id="store-1")
        session.add(order)
        session.commit()

        with patch.object(order_service, "cancel_qpmn_shipment", return_value={"success": True}) as mock_shipment, \
             patch.object(order_service, "cancel_qpmn_order") as mock_order, \
             patch("app.tasks.notifications.notify_order_status_email"):
            order_service.cancel_order(session, order)

        mock_shipment.assert_called_once_with(order)
        mock_order.assert_not_called()
        assert order.status == OrderStatus.CANCELLED

    def test_cancel_order_uses_order_cancel_api_before_reviewed(self):
        session = self._sqlite_session()
        order = Order(order_id="o2", source_account="a", source_order_id="s2", status=OrderStatus.RECEIVED, store_id="store-1")
        session.add(order)
        session.commit()

        with patch.object(order_service, "cancel_qpmn_order", return_value={"success": True}) as mock_order, \
             patch.object(order_service, "cancel_qpmn_shipment") as mock_shipment, \
             patch("app.tasks.notifications.notify_order_status_email"):
            order_service.cancel_order(session, order)

        mock_order.assert_called_once_with(order)
        mock_shipment.assert_not_called()
        assert order.status == OrderStatus.CANCELLED


class TestBuildQpmnReturnPayload:
    """TI-71/72: translate OMS's return request shape into QPMN §3.5's body."""

    def test_full_return_omits_items(self):
        payload = _build_qpmn_return_payload({
            "reason": "damaged", "isFullReturn": True,
            "trackingNumber": "1Z1", "carrierName": "FedEx",
        })
        assert "items" not in payload
        assert payload["reason"] == "damaged"
        assert payload["returnShipments"] == [{"trackingNumber": "1Z1", "carrierName": "FedEx"}]

    def test_full_return_without_tracking_omits_return_shipments(self):
        payload = _build_qpmn_return_payload({"reason": "damaged", "isFullReturn": True})
        assert "returnShipments" not in payload
        assert payload == {"reason": "damaged"}

    def test_defaults_to_full_return_when_unspecified(self):
        # isFullReturn defaults to True on the OMS side too.
        assert _build_qpmn_return_payload({"reason": "damaged"}) == {"reason": "damaged"}

    def test_partial_return_rejected(self):
        with pytest.raises(ValueError, match="Partial return"):
            _build_qpmn_return_payload({
                "reason": "damaged", "isFullReturn": False,
                "items": [{"sku": "PC-123", "quantity": 1}],
            })

