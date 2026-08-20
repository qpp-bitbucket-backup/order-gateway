"""Client for QPMN's webhook subscription management API (§4).

Thin proxy only — webhook registrations aren't persisted locally, QPMN is
the source of truth (per Ivan, 2026-08-11).
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.sentry_alerts import ALERTS, capture_integration_alert

logger = logging.getLogger(__name__)

_WEBHOOK_MGMT_ALERTS = ALERTS["webhook_mgmt"]


def _capture_qpmn_alert(alert_key: str, **format_args) -> None:
    """Capture a QPMN webhook-management API failure to Sentry."""
    capture_integration_alert(_WEBHOOK_MGMT_ALERTS[alert_key], format_args=format_args, component="qpmn_api")


class QpmnApiError(Exception):
    """Raised when QPMN's webhook API returns a non-2xx / success=false response."""

    def __init__(self, status_code: int, message: str, code: Optional[str] = None):
        self.status_code = status_code
        self.message = message
        self.code = code
        super().__init__(message)


def _request(method: str, path: str, store_key: str, **kwargs) -> Dict[str, Any]:
    url = f"{settings.QPMN_OPEN_API_URL.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Authorization": f"Basic {store_key}"}

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.request(method, url, headers=headers, **kwargs)
    except httpx.TimeoutException as exc:
        logger.error("[qpmn_webhook] %s %s timed out: %s", method, path, exc)
        _capture_qpmn_alert("TIMEOUT", method=method, path=path, exc=exc)
        raise QpmnApiError(status_code=504, message=f"QPMN webhook API timeout: {exc}")
    except Exception as exc:
        logger.error("[qpmn_webhook] %s %s request failed: %s", method, path, exc)
        _capture_qpmn_alert("REQUEST_FAILED", method=method, path=path, exc=exc)
        raise QpmnApiError(status_code=502, message=f"QPMN webhook API request failed: {exc}")

    # Non-JSON bodies (e.g. an HTML/plain-text error page from an upstream
    # proxy on a 405/502) used to crash here with an unhandled JSONDecodeError.
    try:
        body = response.json() if response.content else {}
    except ValueError:
        logger.error(
            "[qpmn_webhook] Non-JSON response for %s %s (HTTP %s): %s",
            method, path, response.status_code, response.text[:500],
        )
        _capture_qpmn_alert(
            "NON_JSON_RESPONSE",
            method=method, path=path, status_code=response.status_code, text=response.text[:200],
        )
        body = {}

    if response.status_code >= 400 or not body.get("success", True):
        error = body.get("data") if isinstance(body.get("data"), dict) else {}
        message = error.get("message", f"QPMN webhook API returned HTTP {response.status_code}")
        _capture_qpmn_alert(
            "REJECTED",
            method=method, path=path, status_code=response.status_code, error_message=message,
        )
        raise QpmnApiError(
            status_code=response.status_code,
            message=message,
            code=error.get("code"),
        )

    return body.get("data") or {}


def create_webhook(store_key: str, name: str, url: str, event_types: List[str], enabled: bool) -> Dict[str, Any]:
    """§4.1 新增 Webhook."""
    return _request("POST", "webhook", store_key, json={
        "name": name,
        "url": url,
        "eventTypes": event_types,
        "enabled": enabled,
    })


def list_webhooks(
    store_key: str,
    page: int,
    size: int,
    enabled: Optional[bool] = None,
    name: Optional[str] = None,
    event_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """§4.2 查询店铺所有 Webhook."""
    params: Dict[str, Any] = {"page": page, "size": size}
    if enabled is not None:
        params["enabled"] = enabled
    if name is not None:
        params["name"] = name
    if event_types:
        params["eventTypes"] = event_types
    return _request("GET", "webhook", store_key, params=params)


def get_webhook(store_key: str, webhook_id: int) -> Dict[str, Any]:
    """§4.3 查询店铺指定 Webhook."""
    return _request("GET", f"webhook/{webhook_id}", store_key)


def update_webhook(store_key: str, webhook_id: int, name: str, url: str, event_types: List[str], enabled: bool) -> Dict[str, Any]:
    """§4.4 更新 Webhook."""
    return _request("PUT", f"webhook/{webhook_id}", store_key, json={
        "name": name,
        "url": url,
        "eventTypes": event_types,
        "enabled": enabled,
    })


def delete_webhook(store_key: str, webhook_id: int) -> Dict[str, Any]:
    """§4.5 删除 Webhook."""
    return _request("DELETE", f"webhook/{webhook_id}", store_key)
