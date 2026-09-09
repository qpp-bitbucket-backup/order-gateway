"""Unit tests for health checks (app/core/health.py) — all IO mocked."""
from unittest.mock import patch

import pika

from app.core import health


def _db_mock(first_value=None, exc=None):
    """Build a patched app.core.health.Session context manager mock."""
    patcher = patch("app.core.health.Session")
    session_cls = patcher.start()
    ctx = session_cls.return_value.__enter__.return_value
    ctx.exec.return_value.first.return_value = first_value
    if exc is not None:
        session_cls.side_effect = exc
    return patcher, session_cls


class TestCheckDatabaseHealth:
    def test_healthy_when_select_one_returns_row(self):
        patcher, _ = _db_mock(first_value=(1,))
        try:
            result = health.check_database_health()
        finally:
            patcher.stop()
        assert result["status"] == "healthy"
        assert result["response_time_ms"] >= 0

    def test_unhealthy_when_no_result(self):
        patcher, _ = _db_mock(first_value=None)
        try:
            result = health.check_database_health()
        finally:
            patcher.stop()
        assert result["status"] == "unhealthy"
        assert "no result" in result["message"]

    def test_unhealthy_on_exception(self):
        patcher, _ = _db_mock(exc=RuntimeError("connection refused"))
        try:
            result = health.check_database_health()
        finally:
            patcher.stop()
        assert result["status"] == "unhealthy"
        assert "connection refused" in result["message"]


class TestCheckRabbitmqHealth:
    def _patched_connection(self):
        patcher = patch.object(health.pika, "BlockingConnection")
        conn_cls = patcher.start()
        return patcher, conn_cls

    def test_healthy_when_connection_and_channel_ok(self):
        params_patcher = patch("app.core.health.get_rabbitmq_connection_params")
        params_patcher.start()
        patcher, conn_cls = self._patched_connection()
        try:
            conn = conn_cls.return_value
            conn.is_open = True
            result = health.check_rabbitmq_health()
        finally:
            patcher.stop()
            params_patcher.stop()
        assert result["status"] == "healthy"
        conn.channel.return_value.close.assert_called_once()

    def test_unhealthy_when_not_open(self):
        params_patcher = patch("app.core.health.get_rabbitmq_connection_params")
        params_patcher.start()
        patcher, conn_cls = self._patched_connection()
        try:
            conn_cls.return_value.is_open = False
            result = health.check_rabbitmq_health()
        finally:
            patcher.stop()
            params_patcher.stop()
        assert result["status"] == "unhealthy"
        assert "could not be opened" in result["message"]

    def test_unhealthy_on_amqp_error(self):
        params_patcher = patch("app.core.health.get_rabbitmq_connection_params")
        params_patcher.start()
        patcher, conn_cls = self._patched_connection()
        try:
            conn_cls.side_effect = pika.exceptions.AMQPConnectionError()
            result = health.check_rabbitmq_health()
        finally:
            patcher.stop()
            params_patcher.stop()
        assert result["status"] == "unhealthy"

    def test_unhealthy_on_generic_error(self):
        params_patcher = patch("app.core.health.get_rabbitmq_connection_params")
        params_patcher.start()
        patcher, conn_cls = self._patched_connection()
        try:
            conn_cls.side_effect = OSError("socket closed")
            result = health.check_rabbitmq_health()
        finally:
            patcher.stop()
            params_patcher.stop()
        assert result["status"] == "unhealthy"
        assert "socket closed" in result["message"]

    def test_open_connection_closed_in_finally(self):
        params_patcher = patch("app.core.health.get_rabbitmq_connection_params")
        params_patcher.start()
        patcher, conn_cls = self._patched_connection()
        try:
            conn = conn_cls.return_value
            conn.is_open = True
            conn.channel.side_effect = RuntimeError("channel blew up")
            result = health.check_rabbitmq_health()
        finally:
            patcher.stop()
            params_patcher.stop()
        assert result["status"] == "unhealthy"
        conn.close.assert_called_once()


_HEALTHY_DB = {"status": "healthy", "message": "ok", "response_time_ms": 1.0}
_UNHEALTHY_DB = {"status": "unhealthy", "message": "down", "response_time_ms": 0}
_HEALTHY_MQ = {"status": "healthy", "message": "ok", "response_time_ms": 1.0}
_UNHEALTHY_MQ = {"status": "unhealthy", "message": "down", "response_time_ms": 0}


class TestGetFullHealthStatus:
    def test_all_healthy(self):
        with patch.object(health, "check_database_health", return_value=dict(_HEALTHY_DB)), \
             patch.object(health, "check_rabbitmq_health", return_value=dict(_HEALTHY_MQ)):
            result = health.get_full_health_status()
        assert result["status"] == "healthy"
        assert result["status_code"] == 200
        assert set(result["components"]) == {"database", "rabbitmq"}
        assert result["timestamp"]

    def test_degraded_when_db_down(self):
        with patch.object(health, "check_database_health", return_value=dict(_UNHEALTHY_DB)), \
             patch.object(health, "check_rabbitmq_health", return_value=dict(_HEALTHY_MQ)):
            result = health.get_full_health_status()
        assert result["status"] == "degraded"
        assert result["status_code"] == 503

    def test_degraded_when_rabbitmq_down(self):
        with patch.object(health, "check_database_health", return_value=dict(_HEALTHY_DB)), \
             patch.object(health, "check_rabbitmq_health", return_value=dict(_UNHEALTHY_MQ)):
            result = health.get_full_health_status()
        assert result["status"] == "degraded"
        assert result["status_code"] == 503

    def test_degraded_when_both_down(self):
        with patch.object(health, "check_database_health", return_value=dict(_UNHEALTHY_DB)), \
             patch.object(health, "check_rabbitmq_health", return_value=dict(_UNHEALTHY_MQ)):
            result = health.get_full_health_status()
        assert result["status"] == "degraded"
        assert result["status_code"] == 503
