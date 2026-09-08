"""Unit tests for pure helpers and status sets in app/services/order.py."""
from app.models.order import ORDER_STATE_TRANSITIONS, OrderStatus, can_transition
from app.services.order import (
    DELETABLE_STATUSES,
    REPUBLISHABLE_STATUSES,
    UPDATABLE_STATUSES,
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
        for terminal in (OrderStatus.CANCELLED, OrderStatus.SHIPPED):
            assert ORDER_STATE_TRANSITIONS[terminal] == []

    def test_can_transition_matches_table(self):
        for from_status, allowed in ORDER_STATE_TRANSITIONS.items():
            for to_status in OrderStatus:
                assert can_transition(from_status, to_status) == (to_status in allowed)

    def test_every_status_has_entry(self):
        assert set(ORDER_STATE_TRANSITIONS) == set(OrderStatus)
