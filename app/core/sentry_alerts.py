"""Centralized Sentry alert capture for QPMN/OMS/VFS integration failures.

Kept dependency-free (only sentry_sdk) so every call site can import it
without risking a circular import — several of the modules that need this
(app/services/order.py, app/services/file.py, app/tasks/orders.py, ...)
already import each other.

Alert definitions (level / failure_type / message template) live in
``ALERTS`` below, grouped by category, rather than as literal strings at
each of the ~34 call sites — one place to see every alert this app can
raise, and to keep wording/level/failure_type consistent.
"""
from typing import Any, Dict, Optional

import sentry_sdk

SENTRY_LEVEL_WARNING = "warning"
SENTRY_LEVEL_ERROR = "error"

ALERTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "push": {
        "RETRY_503": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_retry",
            "message": "push_order: QPMN returned 503 for order {order_id}, first retry scheduled",
        },
        "RETRY_TIMEOUT": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_retry",
            "message": "push_order: QPMN timeout for order {order_id}, first retry scheduled",
        },
        "RETRY_EXHAUSTED_503": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_retry_exhausted",
            "message": "push_order: QPMN returned 503 for order {order_id}, retries exhausted ({max_retries})",
        },
        "RETRY_EXHAUSTED_TIMEOUT": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_retry_exhausted",
            "message": "push_order: QPMN timeout for order {order_id}, retries exhausted ({max_retries})",
        },
        "REJECTED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_rejected",
            "message": "push_order: QPMN rejected order {order_id}: {error_message}",
        },
    },
    "cancel": {
        "NO_STORE_KEY": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_cancel_no_store_key",
            "message": "cancel_qpmn_order: no store_key configured for store_id={store_id} (order {order_id})",
        },
        "TIMEOUT": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_cancel_timeout",
            "message": "cancel_qpmn_order: QPMN API timeout for order {order_id}",
        },
        "REQUEST_FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_cancel_request_failed",
            "message": "cancel_qpmn_order: request failed for order {order_id}: {exc}",
        },
        "REJECTED_HTTP": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_cancel_rejected",
            "message": "cancel_qpmn_order: QPMN returned HTTP {status_code} for order {order_id}: {error_body}",
        },
        "REJECTED_SUCCESS_FALSE": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_cancel_rejected",
            "message": "cancel_qpmn_order: QPMN returned success=false for order {order_id}: {body}",
        },
    },
    "shipping": {
        "NO_STORE_KEY": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_shipping_no_store_key",
            "message": "fetch_shipping_method_from_qpmn: no store_key for store_id={store_id}, falling back to 'Standard'",
        },
        "EMPTY_RESPONSE": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_shipping_empty_response",
            "message": "fetch_shipping_method_from_qpmn: no storeDefaultShippings for store_id={store_id}, falling back to 'Standard'",
        },
        "FETCH_FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_shipping_fetch_failed",
            "message": "fetch_shipping_method_from_qpmn: failed for store_id={store_id}: {exc}, falling back to 'Standard'",
        },
    },
    "currency": {
        "NO_STORE_KEY": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_currency_no_store_key",
            "message": "fetch_currency_from_qpmn: no store_key for store_id={store_id}, falling back to 'CNY'",
        },
        "EMPTY_RESPONSE": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_currency_empty_response",
            "message": "fetch_currency_from_qpmn: no currencyCode for store_id={store_id}, falling back to 'CNY'",
        },
        "FETCH_FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_currency_fetch_failed",
            "message": "fetch_currency_from_qpmn: failed for store_id={store_id}: {exc}, falling back to 'CNY'",
        },
    },
    "file": {
        "DOWNLOAD_FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_file_download_failed",
            "message": "download_file: failed to download design file {url}: {error_msg}",
        },
        "UPLOAD_EMPTY_RESPONSE": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_file_upload_empty_response",
            "message": "upload_to_qpmn: QPMN returned no data for {filename} (HTTP {status_code}): {result}",
        },
        "UPLOAD_FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_file_upload_failed",
            "message": "upload_to_qpmn: failed to upload {file_path}: {exc}",
        },
    },
    "webhook_mgmt": {
        "TIMEOUT": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_webhook_timeout",
            "message": "qpmn_webhook: {method} {path} timed out: {exc}",
        },
        "REQUEST_FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_webhook_request_failed",
            "message": "qpmn_webhook: {method} {path} request failed: {exc}",
        },
        "NON_JSON_RESPONSE": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_webhook_non_json_response",
            "message": "qpmn_webhook: {method} {path} returned non-JSON body (HTTP {status_code}): {text}",
        },
        "REJECTED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_webhook_rejected",
            "message": "qpmn_webhook: {method} {path} failed (HTTP {status_code}): {error_message}",
        },
    },
    "product_sync": {
        "FAILED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_product_sync_failed",
            "message": "sync_all_stores_products: product sync failed for store {store_id}: {exc}",
        },
    },
    "webhook_inbound": {
        "UNMAPPED_EVENT_TYPE": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_webhook_unmapped_event_type",
            "message": "receive_order_status: unmapped event type '{event_type}'",
        },
        "MISSING_ORDER_ID": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "qpmn_webhook_missing_order_id",
            "message": "receive_order_status: missing orderId on '{event_type}' event body (item_id={item_id})",
        },
        "ORDER_NOT_FOUND": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_webhook_order_not_found",
            "message": "receive_order_status: no local order for QPMN orderId={qpmn_order_id} (event='{event_type}')",
        },
        "INVALID_TRANSITION": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "qpmn_webhook_invalid_transition",
            "message": "receive_order_status: invalid transition for order {order_id}: {from_status} -> {to_status}",
        },
    },
    "oms": {
        "ORDER_NOT_FOUND": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "oms_order_not_found",
            "message": "notify_oms: order {order_id} not found for webhook_log {webhook_log_id}",
        },
        "REJECTED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "oms_rejected",
            "message": "notify_oms: OMS rejected order {order_id}: {error_message}",
        },
        "RETRY": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "oms_retry",
            "message": "notify_oms: failed for order {order_id}, first retry scheduled: {exc}",
        },
        "RETRY_EXHAUSTED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "oms_retry_exhausted",
            "message": "notify_oms: exhausted {max_retries} retries for order {order_id}: {exc}",
        },
    },
    "vfs": {
        "ORDER_NOT_FOUND": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "vfs_order_not_found",
            "message": "notify_vfs: order {order_id} not found for webhook_log {webhook_log_id}",
        },
        "REJECTED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "vfs_rejected",
            "message": "notify_vfs: VFS rejected postback for order {order_id}: {error_message}",
        },
        "RETRY": {
            "level": SENTRY_LEVEL_WARNING,
            "failure_type": "vfs_retry",
            "message": "notify_vfs: failed for order {order_id}, first retry scheduled: {exc}",
        },
        "RETRY_EXHAUSTED": {
            "level": SENTRY_LEVEL_ERROR,
            "failure_type": "vfs_retry_exhausted",
            "message": "notify_vfs: exhausted {max_retries} retries for order {order_id}: {exc}",
        },
    },
}


def capture_integration_alert(
    alert_def: Dict[str, str],
    order_id: Optional[str] = None,
    format_args: Optional[Dict[str, Any]] = None,
    **category_tags: str,
) -> None:
    """Capture an external-integration (QPMN/OMS/VFS) failure to Sentry.

    ``alert_def`` is one entry from ``ALERTS`` (e.g. ``ALERTS["push"]["REJECTED"]``),
    containing ``level``, ``failure_type``, and a ``message`` template —
    ``format_args`` fills in that template's placeholders.

    ``category_tags`` are extra Sentry tags for filtering/alert-routing,
    e.g. ``component="qpmn_api"``, ``order_queue="order_pushing"``,
    ``channel="oms"`` — pass whichever dimension identifies the call site.

    ``fingerprint`` is pinned to (*category_tags.values(), failure_type)
    rather than the default message-text grouping, so events aggregate into
    one Sentry issue per failure type instead of one per order_id.
    """
    failure_type = alert_def["failure_type"]
    message = alert_def["message"].format(**(format_args or {}))

    with sentry_sdk.new_scope() as scope:
        for key, value in category_tags.items():
            scope.set_tag(key, value)
        scope.set_tag("failure_type", failure_type)
        if order_id:
            scope.set_tag("order_id", order_id)
        scope.fingerprint = [*category_tags.values(), failure_type]
        sentry_sdk.capture_message(message, level=alert_def["level"])
