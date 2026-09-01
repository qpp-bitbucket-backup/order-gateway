"""
Real (non-mocked) Sentry connectivity check — actually sends events to the
configured SENTRY_DSN, unlike scripts/test_push_retry.py which mocks Sentry
out entirely.

Sends 4 events tagged the same way push_order's actual Sentry capture points
do (app/tasks/orders.py: _capture_push_alert() + the outer except block), so
they're a direct dry run for the Rule 1 (warning) / Rule 2 (error) alert-rule
conditions:
    - warning / failure_type=qpmn_retry            (first retry scheduled)     [capture_message]
    - error   / failure_type=qpmn_retry_exhausted  (retries capped out)        [capture_message]
    - error   / failure_type=qpmn_rejected         (QPMN returned success=false) [capture_message]
    - error   / failure_type=unexpected_exception  (real code bug)             [capture_exception]

Each uses a distinct fake order id per run so Sentry treats them as separate
issues instead of merging into a prior run's issue — except
unexpected_exception, which is captured via capture_exception and will keep
grouping into the same issue across runs (same stack trace every time); this
matches real behavior, since repeat hits of the same code bug should stay one
issue until it's fixed.

All 4 also carry test_run_id + source=test_sentry_connection.py, so they're
easy to find/filter in the Sentry UI:
    https://prod-qpmn-cn-sentry.qppdev.com/organizations/sentry/projects/order-gateway/?project=38

Usage:
    python scripts/test_sentry_connection.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

if not settings.SENTRY_DSN:
    print("SENTRY_DSN is not set in .env — nothing to test.")
    sys.exit(1)

import sentry_sdk

run_id = uuid.uuid4().hex[:8]
print(f"Initializing Sentry (environment={settings.SENTRY_ENVIRONMENT}, run_id={run_id}) ...")

sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    environment=settings.SENTRY_ENVIRONMENT,
    traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
)


def send(level: str, failure_type: str, message: str, as_exception: bool = False):
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("test_run_id", run_id)
        scope.set_tag("source", "test_sentry_connection.py")
        scope.set_tag("order_queue", "order_pushing")
        scope.set_tag("failure_type", failure_type)
        print(f"Sending {level}/{failure_type}...")
        if as_exception:
            try:
                raise RuntimeError(message)
            except RuntimeError as e:
                sentry_sdk.capture_exception(e)
        else:
            sentry_sdk.capture_message(message, level=level)


send(
    "warning",
    "qpmn_retry",
    f"push_order: QPMN returned 503 for order TEST-{run_id}-A, first retry scheduled (run_id={run_id})",
)
send(
    "error",
    "qpmn_retry_exhausted",
    f"push_order: QPMN returned 503 for order TEST-{run_id}-B, retries exhausted (5) (run_id={run_id})",
)
send(
    "error",
    "qpmn_rejected",
    f"push_order: QPMN rejected order TEST-{run_id}-C: mock rejection reason (run_id={run_id})",
)
# unexpected_exception is the one real capture point that does use
# capture_exception (push_order's outer except block) — grouping by stack
# trace here is correct/expected in production: repeat hits of the exact
# same bug should stay one issue until the code is fixed.
send(
    "error",
    "unexpected_exception",
    f"push_order: unexpected error for order TEST-{run_id}-D (run_id={run_id})",
    as_exception=True,
)

print("Flushing (waiting up to 5s for delivery)...")
sentry_sdk.flush(timeout=5)

print()
print(f"Done. In Sentry, filter by tag test_run_id:{run_id} (or search '{run_id}') to confirm all 4 events arrived.")
print("Note: unexpected_exception will show as a REGRESSION on every run after the first (same stack trace each time) — that's expected, not a bug.")
print("If nothing shows up after a minute: check network reachability to the Sentry host, and that project 38 is correct.")
