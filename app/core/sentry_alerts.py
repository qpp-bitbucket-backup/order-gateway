"""Centralized Sentry alert capture for QPMN/OMS/VFS integration failures.

Kept dependency-free (only sentry_sdk + stdlib) so every call site can
import it without risking a circular import — several of the modules that
need this (app/services/order.py, app/services/file.py, app/tasks/orders.py,
...) already import each other.

Alert definitions (level / failure_type / message template) live in
sentry_alert_definitions.json, grouped by category, rather than as literal
strings at each of the ~34 call sites — one place to see every alert this
app can raise, and to keep wording/level/failure_type consistent.
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional

import sentry_sdk

SENTRY_LEVEL_WARNING = "warning"
SENTRY_LEVEL_ERROR = "error"

_DEFINITIONS_PATH = Path(__file__).with_name("sentry_alert_definitions.json")
with open(_DEFINITIONS_PATH, encoding="utf-8") as f:
    ALERTS: Dict[str, Dict[str, Dict[str, str]]] = json.load(f)


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
