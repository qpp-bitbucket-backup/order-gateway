"""Client for QPMN's webhook subscription management API (§4).

Thin proxy only — webhook registrations aren't persisted locally, QPMN is
the source of truth (per Ivan, 2026-08-11).
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


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

    with httpx.Client(timeout=15.0) as client:
        response = client.request(method, url, headers=headers, **kwargs)

    body = response.json() if response.content else {}

    if response.status_code >= 400 or not body.get("success", True):
        error = body.get("data") if isinstance(body.get("data"), dict) else {}
        raise QpmnApiError(
            status_code=response.status_code,
            message=error.get("message", f"QPMN webhook API returned HTTP {response.status_code}"),
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
