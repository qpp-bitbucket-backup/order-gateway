"""Unit tests for the level-based order status notification system."""
import pytest
from pydantic import ValidationError

from app.models.notification import (
    EMAIL_MUTED_STATUSES,
    STATUS_NOTIFICATION_LEVEL,
    NotificationLevel,
    status_notification_level,
)
from app.models.order import Order, OrderStatus
from app.schemas.client import NotificationConfig, NotificationLevelSetting
from app.services.email_templates import _format_duration, render_order_status_email
from app.services.order_notifications import (
    enqueue_status_change_email,
    record_status_change,
    resolve_notification_recipients,
)

def _order(status: OrderStatus = OrderStatus.PENDING) -> Order:
    return Order(
        order_id="test-order-001",
        source_account="test_account",
        source_order_id="src-order-001",
        status=status,
    )


class TestStatusNotificationLevel:
    def test_every_order_status_has_an_explicit_level(self):
        """Full coverage: adding an OrderStatus without a level mapping must
        fail here, forcing a conscious level decision."""
        assert set(STATUS_NOTIFICATION_LEVEL.keys()) == set(OrderStatus)

    def test_cancelled_is_warning(self):
        assert status_notification_level(OrderStatus.CANCELLED) is NotificationLevel.WARNING

    def test_failed_and_errored_are_error(self):
        assert status_notification_level(OrderStatus.FAILED) is NotificationLevel.ERROR
        assert status_notification_level(OrderStatus.ERRORED) is NotificationLevel.ERROR

    def test_normal_flow_is_info(self):
        for status in (
            OrderStatus.RECEIVED, OrderStatus.PENDING, OrderStatus.VALIDATED,
            OrderStatus.COOLING_OFF, OrderStatus.PROCESSING, OrderStatus.PRINTREADY,
            OrderStatus.PRINTED, OrderStatus.PRODUCED, OrderStatus.SHIPPED,
        ):
            assert status_notification_level(status) is NotificationLevel.INFO


class TestRecordStatusChange:
    def test_sets_status_and_appends_level_tagged_log(self):
        order = _order(OrderStatus.RECEIVED)
        from_status, level = record_status_change(
            order, OrderStatus.PENDING, "order_publishing_started",
            "Order publishing started by celery worker", worker="celery_worker",
        )
        assert from_status is OrderStatus.RECEIVED
        assert level is NotificationLevel.INFO
        assert order.status is OrderStatus.PENDING
        entry = order.logs[-1]
        assert entry["action"] == "order_publishing_started"
        assert entry["message"] == "Order publishing started by celery worker"
        assert entry["level"] == "info"
        assert entry["worker"] == "celery_worker"
        assert "timestamp" in entry

    def test_returns_error_level_for_failed_transition(self):
        order = _order(OrderStatus.PROCESSING)
        from_status, level = record_status_change(
            order, OrderStatus.FAILED, "order_processing_failed", "boom",
        )
        assert from_status is OrderStatus.PROCESSING
        assert level is NotificationLevel.ERROR
        assert order.logs[-1]["level"] == "error"

    def test_extra_fields_merged_into_log_entry(self):
        order = _order(OrderStatus.PRINTED)
        record_status_change(
            order, OrderStatus.PRODUCED, "qpmn_status_webhook", "all items produced",
            worker="webhook_handler", extra={"event_status": "order_item_produced"},
        )
        assert order.logs[-1]["event_status"] == "order_item_produced"

    def test_worker_omitted_when_not_given(self):
        order = _order(OrderStatus.RECEIVED)
        record_status_change(order, OrderStatus.CANCELLED, "order_cancelled", "cancelled")
        assert "worker" not in order.logs[-1]

    def test_initializes_null_logs_list(self):
        order = _order()
        order.logs = None
        record_status_change(order, OrderStatus.VALIDATED, "order_validated", "ok")
        assert len(order.logs) == 1


class TestResolveNotificationRecipients:
    def test_missing_config_yields_no_recipients(self):
        assert resolve_notification_recipients(None, NotificationLevel.ERROR) == []

    def test_empty_config_yields_no_recipients(self):
        assert resolve_notification_recipients({}, NotificationLevel.ERROR) == []

    def test_disabled_level_yields_no_recipients(self):
        config = {"error": {"enabled": False, "emails": ["ops@example.com"]}}
        assert resolve_notification_recipients(config, NotificationLevel.ERROR) == []

    def test_enabled_level_with_no_emails_yields_no_recipients(self):
        config = {"error": {"enabled": True, "emails": []}}
        assert resolve_notification_recipients(config, NotificationLevel.ERROR) == []

    def test_enabled_level_returns_its_own_emails_only(self):
        config = {
            "info": {"enabled": True, "emails": ["info@example.com"]},
            "error": {"enabled": True, "emails": ["ops@example.com", "alert@example.com"]},
        }
        assert resolve_notification_recipients(config, NotificationLevel.ERROR) == [
            "ops@example.com", "alert@example.com",
        ]
        # warning has no entry at all -> disabled
        assert resolve_notification_recipients(config, NotificationLevel.WARNING) == []


class TestRenderOrderStatusEmail:
    def test_subject_carries_level_and_target_status(self):
        # No source_order_id given -> falls back to the internal order_id;
        # "[LEVEL] Order {id} {Status}" style, target status title-cased
        subject, _, _ = render_order_status_email(
            order_id="ord-1", from_status="processing", to_status="failed",
            level="error", message="boom",
        )
        assert subject == "[ERROR] Order ord-1 Failed"

        # Underscored statuses render with spaces: cooling_off -> Cooling Off
        subject, _, _ = render_order_status_email(
            order_id="ord-1", from_status="received", to_status="cooling_off",
            level="warning", message="wait",
        )
        assert subject == "[WARNING] Order ord-1 Cooling Off"

    def test_subject_prefers_source_order_id(self):
        subject, text, _ = render_order_status_email(
            order_id="3f2c8e90-uuid", from_status="processing", to_status="failed",
            level="error", message="boom", source_order_id="SHOP-2026-1001",
        )
        assert subject == "[ERROR] Order SHOP-2026-1001 Failed"
        assert "Source Order ID: SHOP-2026-1001" in text

    def test_html_escapes_user_controlled_values(self):
        _, _, html = render_order_status_email(
            order_id="ord-1", from_status="validated", to_status="cancelled",
            level="warning", message='<script>alert("x")</script>',
            store_name='Topps"><b>',
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert 'Topps&quot;&gt;&lt;b&gt;' in html

    def test_text_body_lists_fields(self):
        _, text, _ = render_order_status_email(
            order_id="ord-1", from_status="received", to_status="pending",
            level="info", message="queued", store_name="Topps UK",
        )
        assert "Order ID: ord-1" in text
        assert "Store: Topps UK" in text
        assert "Status Change: received -> pending" in text

    def test_view_details_button_rendered_when_url_given(self):
        url = "https://admin.example.com/admin/orders/show/ord-1"
        _, text, html = render_order_status_email(
            order_id="ord-1", from_status="received", to_status="pending",
            level="info", message="queued", view_url=url,
        )
        assert f'href="{url}"' in html
        assert ">View Details</a>" in html
        # Button styling matches the password reset button exactly
        assert "background-color:#4f46e5" in html
        assert "padding:10px 24px" in html
        assert "border-radius:4px" in html
        assert f"View Details: {url}" in text

    def test_no_button_without_view_url(self):
        _, text, html = render_order_status_email(
            order_id="ord-1", from_status="received", to_status="pending",
            level="info", message="queued",
        )
        assert "View Details" not in html
        assert "View Details" not in text

    def test_header_background_follows_level(self):
        # "QPMN Order Gateway" header tint: info blue / warning brown /
        # error dark red; unknown or empty level falls back to the default.
        expected = {
            "info": "#0842a0",
            "warning": "#bf8e24",
            "error": "#c10d0d",
        }
        for level, bg in expected.items():
            _, _, html = render_order_status_email(
                order_id="ord-1", from_status="received", to_status="shipped",
                level=level, message="m",
            )
            assert f"background-color:{bg};padding:20px 32px" in html
        _, _, html = render_order_status_email(
            order_id="ord-1", from_status="received", to_status="shipped",
            level="", message="m",
        )
        assert "background-color:#0842a0;padding:20px 32px" in html

    def test_duration_formatting(self):
        assert _format_duration(86400) == "1 day"
        assert _format_duration(172800) == "2 days"
        assert _format_duration(129600) == "1 day 12 hours"
        assert _format_duration(5400) == "1 hour 30 minutes"
        assert _format_duration(300) == "5 minutes"
        assert _format_duration(45) == "45 seconds"

    def test_cooling_off_period_rendered_when_duration_given(self):
        subject, text, html = render_order_status_email(
            order_id="ord-1", from_status="validated", to_status="cooling_off",
            level="info", message="holding", cooling_off_seconds=172800,
        )
        assert subject == "[INFO] Order ord-1 Cooling Off"
        assert "Cooling-off Period: 2 days" in text
        assert "Cooling-off Period" in html
        assert "2 days" in html

    def test_no_cooling_off_line_without_duration(self):
        _, text, html = render_order_status_email(
            order_id="ord-1", from_status="received", to_status="pending",
            level="info", message="m",
        )
        assert "Cooling-off Period" not in text
        assert "Cooling-off Period" not in html

    def test_empty_from_status_renders_as_new(self):
        # create_order dispatches the initial RECEIVED notification with
        # from_status=None (no previous status) — must render as "new",
        # not a bare " -> received".
        subject, text, html = render_order_status_email(
            order_id="ord-9", from_status="", to_status="received",
            level="info", message="Order received from shop",
        )
        assert subject == "[INFO] Order ord-9 Received"
        assert "Status Change: new -> received" in text
        assert "new -&gt; received" in html


class TestEmailMutedStatuses:
    """PENDING/PROCESSING transitions never dispatch an email (high-frequency
    intermediate states); COOLING_OFF stays notifyable."""

    def test_mute_set_membership(self):
        assert OrderStatus.PENDING in EMAIL_MUTED_STATUSES
        assert OrderStatus.PROCESSING in EMAIL_MUTED_STATUSES
        assert OrderStatus.COOLING_OFF not in EMAIL_MUTED_STATUSES

    def test_enqueue_returns_none_for_muted_statuses(self, monkeypatch):
        from app.tasks.notifications import notify_order_status_email

        dispatched: list = []
        monkeypatch.setattr(
            notify_order_status_email, "delay",
            lambda **kw: dispatched.append(kw),
        )
        order = _order(OrderStatus.RECEIVED)
        for muted in (OrderStatus.PENDING, OrderStatus.PROCESSING):
            # session is never touched on the muted path — None is fine
            assert enqueue_status_change_email(
                None, order, OrderStatus.RECEIVED, muted,
                NotificationLevel.INFO, "muted transition",
            ) is None
        assert not dispatched

    def test_enqueue_creates_row_for_cooling_off(self, monkeypatch, db_engine):
        from sqlmodel import Session

        from app.models.notification import NotificationEmailLog
        from app.tasks.notifications import notify_order_status_email

        dispatched: list = []
        monkeypatch.setattr(
            notify_order_status_email, "delay",
            lambda **kw: dispatched.append(kw),
        )
        order = _order(OrderStatus.VALIDATED)
        order.store_id = "notify-test-store"
        with Session(db_engine) as session:
            log_id = enqueue_status_change_email(
                session, order, OrderStatus.VALIDATED, OrderStatus.COOLING_OFF,
                NotificationLevel.INFO,
                "Order holding in cooling-off for 172800s",
            )
            try:
                assert log_id is not None
                nlog = session.get(NotificationEmailLog, log_id)
                assert nlog is not None
                assert nlog.to_status == "cooling_off"
                assert dispatched and dispatched[0]["log_id"] == log_id
            finally:
                nlog = session.get(NotificationEmailLog, log_id)
                if nlog is not None:
                    session.delete(nlog)
                session.commit()


class TestCreateOrderNotification:
    """create_order must seed the initial RECEIVED log entry and dispatch
    the email notification task (regression: new orders created via the API
    silently produced no notification because the constructor-style status
    assignment was not among the transition call sites)."""

    def test_create_order_seeds_log_and_dispatches_notification(self, monkeypatch, db_engine):
        from sqlmodel import Session, select

        from app.models.notification import NotificationEmailLog
        from app.services.order import order_service, publish_order
        from app.tasks.notifications import notify_order_status_email

        dispatched: list = []
        monkeypatch.setattr(
            notify_order_status_email, "delay",
            lambda **kw: dispatched.append(kw),
        )
        monkeypatch.setattr(publish_order, "apply_async", lambda *a, **kw: None)

        nlog = None
        with Session(db_engine) as session:
            order = order_service.create_order(
                session,
                source_account="notify-test",
                source_order_id="NTF-CREATE-TEST-1",
                destination={"name": "Notify Test", "email": "n@example.com"},
                order_data={"items": []},
                store_id="notify-test-store",
            )
            try:
                assert order.status is OrderStatus.RECEIVED
                assert order.logs, "initial order_created log entry missing"
                assert order.logs[0]["action"] == "order_created"
                assert order.logs[0]["level"] == "info"

                nlog = session.exec(
                    select(NotificationEmailLog).where(
                        NotificationEmailLog.order_id == order.order_id
                    )
                ).first()
                assert nlog is not None, "no NotificationEmailLog row created"
                assert nlog.from_status is None  # first status, no previous one
                assert nlog.to_status == "received"
                assert nlog.level == "info"
                assert nlog.store_id == "notify-test-store"
                assert dispatched and dispatched[0]["log_id"] == nlog.id
            finally:
                if nlog is not None:
                    session.delete(nlog)
                session.delete(order)
                session.commit()


class TestOrderServiceAppendLog:
    """OrderService.append_log is the single write path for order.logs."""

    def test_entry_shape_and_defaults(self):
        from app.services.order import order_service

        order = _order()
        order.logs = None
        order_service.append_log(order, "order_oms_retry", "retrying")
        assert order.logs and len(order.logs) == 1
        entry = order.logs[0]
        assert entry["action"] == "order_oms_retry"
        assert entry["message"] == "retrying"
        assert entry["level"] == "info"
        assert "worker" not in entry  # optional, omitted when not given
        assert "timestamp" in entry

    def test_worker_and_extra_merge(self):
        from app.services.order import order_service

        order = _order()
        order_service.append_log(
            order, "qpmn_cancel_failed", "boom",
            level="error", worker="platform_api",
            extra={"status_code": 500, "error": "boom"},
        )
        entry = order.logs[-1]
        assert entry["level"] == "error"
        assert entry["worker"] == "platform_api"
        assert entry["status_code"] == 500
        assert entry["error"] == "boom"

    def test_appends_to_existing_logs(self):
        from app.services.order import order_service

        order = _order()
        order_service.append_log(order, "first", "m1")
        order_service.append_log(order, "second", "m2")
        assert [e["action"] for e in order.logs] == ["first", "second"]


class TestTaskRouting:
    """Tasks without a task_routes entry publish to the default "celery"
    queue, which no worker consumes — RabbitMQ silently drops the message
    (no mandatory flag) and the notification_email_logs row stays "received"
    forever with NULL recipients. The @task(queue=...) decorator option does
    NOT participate in routing; only task_routes does."""

    def test_every_app_task_has_explicit_queue_route(self):
        import app.tasks.orders  # noqa: F401  (task registration side effect)
        import app.tasks.notifications  # noqa: F401
        import app.tasks.products  # noqa: F401
        import app.tasks.stats  # noqa: F401
        from app.core.celery import celery_app

        routes = celery_app.conf.task_routes
        app_tasks = sorted(n for n in celery_app.tasks if n.startswith("tasks."))
        assert app_tasks, "no app tasks discovered"
        missing = [n for n in app_tasks if n not in routes]
        assert not missing, f"tasks without explicit queue route (messages silently dropped): {missing}"


class TestBeatSchedule:
    """Every beat_schedule entry must be a non-empty dict with task/schedule:
    an empty dict value ({} ) builds a ScheduleEntry with schedule=None and
    beat crashes at startup with 'NoneType' object has no attribute 'is_due'
    (disabled feature flags must omit the entry entirely)."""

    def test_every_beat_entry_is_constructible(self):
        from celery.beat import ScheduleEntry

        from app.core.celery import celery_app

        schedule = celery_app.conf.beat_schedule
        for name, entry in schedule.items():
            assert isinstance(entry, dict) and entry, f"beat entry {name!r} is empty"
            assert "task" in entry and "schedule" in entry, name
            # Exactly what beat does per tick — must not raise
            ScheduleEntry(**entry).is_due()


class TestAppendOrderLogDefaultLevel:
    """Non-status-change log entries must default to level "info" so every
    order.logs entry carries a level (status-change entries get theirs from
    STATUS_NOTIFICATION_LEVEL)."""

    def test_defaults_to_info(self):
        from app.tasks.orders import _append_order_log

        order = _order()
        _append_order_log(order, "order_oms_retry", "retrying")
        assert order.logs and order.logs[0]["level"] == "info"
        assert order.logs[0]["worker"] == "celery_order_worker"

    def test_level_can_be_overridden(self):
        from app.tasks.orders import _append_order_log

        order = _order()
        _append_order_log(order, "order_push_retry", "retrying", level="error")
        assert order.logs[0]["level"] == "error"


class TestNotificationConfigSchema:
    def test_defaults_disable_every_level(self):
        config = NotificationConfig()
        assert config.info.enabled is False
        assert config.warning.enabled is False
        assert config.error.enabled is False

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            NotificationLevelSetting(enabled=True, emails=["not-an-email"])

    def test_model_dump_shape_matches_storage_format(self):
        config = NotificationConfig(
            error=NotificationLevelSetting(enabled=True, emails=["ops@example.com"]),
        )
        assert config.model_dump() == {
            "info": {"enabled": False, "emails": []},
            "warning": {"enabled": False, "emails": []},
            "error": {"enabled": True, "emails": ["ops@example.com"]},
        }
