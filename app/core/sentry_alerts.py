"""Centralized Sentry alert capture for QPMN/OMS/VFS integration failures.

Kept dependency-free (only sentry_sdk) so every call site can import it
without risking a circular import — several of the modules that need this
(app/services/order.py, app/services/file.py, app/tasks/orders.py, ...)
already import each other.
"""
from typing import Optional

import sentry_sdk


def capture_integration_alert(
    level: str,
    message: str,
    failure_type: str,
    order_id: Optional[str] = None,
    **category_tags: str,
) -> None:
    """Capture an external-integration (QPMN/OMS/VFS) failure to Sentry.

    ``category_tags`` are extra Sentry tags for filtering/alert-routing,
    e.g. ``component="qpmn_api"``, ``order_queue="order_pushing"``,
    ``channel="oms"`` — pass whichever dimension identifies the call site.

    ``fingerprint`` is pinned to (*category_tags.values(), failure_type)
    rather than the default message-text grouping, so events aggregate into
    one Sentry issue per failure type instead of one per order_id.
    """
    with sentry_sdk.new_scope() as scope:
        for key, value in category_tags.items():
            scope.set_tag(key, value)
        scope.set_tag("failure_type", failure_type)
        if order_id:
            scope.set_tag("order_id", order_id)
        scope.fingerprint = [*category_tags.values(), failure_type]
        sentry_sdk.capture_message(message, level=level)
