"""Test the OMS address API (HUB4) for a given orderNo.

Calls oms_service.fetch_order_addresses end-to-end: AES-encrypts the
payload, signs it, POSTs to {OMS_API_URL}, decrypts the
response and persists the addresses (same code path as the validate task).

Usage (from the project root, venv active):
    python scripts/test_oms_api.py <orderNo> [--show-request] [--store-id ID]

Notes:
    - Requires OMS_API_URL / OMS_APP_SECRET / OMS_SOURCE_APP in .env.
    - On success it REPLACES the addresses table rows for that orderNo
      (that is what the service does) — use a test order number if you
      don't want to touch real order addresses.
    - DEBUG=true in .env switches the service to mock data (no HTTP call).
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from app.core.config import settings  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.core import hub4  # noqa: E402
from app.services.oms import oms_service  # noqa: E402
from sqlmodel import Session  # noqa: E402


def show_request(order_no: str) -> None:
    """Print the signed URL params + ciphertext so the call can be replayed
    with curl (does NOT send anything)."""
    payload = {"orderNo": order_no}
    body_ciphertext = hub4.aes_encrypt(
        __import__("json").dumps(payload, ensure_ascii=False),
        settings.OMS_APP_SECRET,
    )
    params = hub4.build_query_params(
        method_name=settings.OMS_ADDRESS_METHOD_NAME,
        source_app=settings.OMS_SOURCE_APP,
        interface_type=settings.OMS_INTERFACE_TYPE,
    )
    sign = hub4.make_sign(params, body_ciphertext, settings.OMS_APP_SECRET)
    url_params = {("Version" if k == "version" else k): v for k, v in params.items()}
    url_params["sign"] = sign
    print("== Request preview (not sent) ==")
    print(f"URL: {settings.OMS_API_URL}")
    print(f"query params: {url_params}")

    qs = "&".join(f"{k}={v}" for k, v in url_params.items())
    print("\n== Replayable curl (bash / WSL) — sign is bound to this exact "
          "requestTime, replay within the gateway's time window ==")
    print(
        f"curl -i -X POST '{settings.OMS_API_URL}?{qs}' \\\n"
        f"  -H 'Content-Type: application/json;charset=UTF-8' \\\n"
        f"  -H 'Accept: application/json' \\\n"
        f"  --data-raw '{body_ciphertext}'"
    )


def raw_call(order_no: str) -> None:
    """Send the signed request directly and dump the full response:
    status, headers, raw body, plus a best-effort AES decrypt of the
    body (HUB4 responses are encrypted with the same appSecret)."""
    import httpx
    import json

    payload = {"orderNo": order_no}
    body_ciphertext = hub4.aes_encrypt(
        json.dumps(payload, ensure_ascii=False), settings.OMS_APP_SECRET
    )
    params = hub4.build_query_params(
        method_name=settings.OMS_ADDRESS_METHOD_NAME,
        source_app=settings.OMS_SOURCE_APP,
        interface_type=settings.OMS_INTERFACE_TYPE,
    )
    sign = hub4.make_sign(params, body_ciphertext, settings.OMS_APP_SECRET)
    url_params = {("Version" if k == "version" else k): v for k, v in params.items()}
    url_params["sign"] = sign

    url = oms_service.base_url
    print(f"== Raw call (bypasses service persistence) ==")
    print(f"POST {url}")
    print(f"query: {url_params}")
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.post(
            url,
            params=url_params,
            content=body_ciphertext,
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json",
            },
        )
    print(f"\nHTTP {resp.status_code}")
    for k, v in resp.headers.items():
        print(f"  {k}: {v}")
    print(f"\n-- raw body --\n{resp.text}")
    try:
        decrypted = hub4.aes_decrypt(resp.text.strip(), settings.OMS_APP_SECRET)
        print(f"\n-- AES-decrypted body --\n{decrypted}")
    except Exception as exc:
        print(f"\n(body is not AES ciphertext with this secret: {exc})")


def dump_address(label, addr):
    if not addr:
        print(f"  {label}: <none>")
        return
    fields = [
        "first_name", "last_name", "company", "country", "state", "city",
        "address1", "address2", "postcode", "phone", "mobile", "email",
    ]
    print(f"  {label}:")
    for f in fields:
        print(f"    {f}: {getattr(addr, f, None)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order_no", help="OMS orderNo to look up")
    parser.add_argument("--show-request", action="store_true",
                        help="print the signed request without sending it")
    parser.add_argument("--raw", action="store_true",
                        help="send the request directly and dump the full "
                             "response (status/headers/body + AES decrypt "
                             "attempt), bypassing service persistence")
    parser.add_argument("--store-id", default=None,
                        help="store_id to resolve default_recipient_email")
    parser.add_argument("--status", default=None, metavar="EVENT_STATUS",
                        help="test OMS API-002 (fc_qp_status_feedback): push "
                             "this status (e.g. printed/shipped) for the order "
                             "identified by the positional arg (order id or "
                             "sourceOrderId)")
    parser.add_argument("--status-desc", default=None,
                        help="statusDesc for --status (defaults to the status)")
    parser.add_argument("--tracking", default=None, metavar="TRACKING_NO",
                        help="with --status (typically shipped): tracking "
                             "number sent in the shipments array. Built in "
                             "the QPMN webhook shape and converted to the "
                             "OMS carrierName/ISO-8601 shipDate shape by the "
                             "service, exactly like the production flow.")
    parser.add_argument("--carrier", default=None, metavar="CARRIER",
                        help="carrier name paired with --tracking "
                             "(defaults to TEST-CARRIER)")
    parser.add_argument("--url", default=None,
                        help="override the gateway URL for this run (e.g. "
                             "https://host/some/path) — no .env change needed "
                             "when trying a candidate URL from the OMS team")
    args = parser.parse_args()

    if args.url:
        oms_service.base_url = args.url.rstrip("/")
        print(f"[override] gateway URL for this run: {oms_service.base_url}")

    if not settings.OMS_API_URL:
        print("OMS_API_URL is not configured — aborting.")
        sys.exit(1)
    print(f"OMS_API_URL          = {settings.OMS_API_URL}")
    print(f"OMS_SOURCE_APP       = {settings.OMS_SOURCE_APP}")
    print(f"OMS_ADDRESS_METHOD   = {settings.OMS_ADDRESS_METHOD_NAME}")
    print(f"OMS_APP_SECRET       = {'***set***' if settings.OMS_APP_SECRET else '<MISSING>'}")
    print(f"USE_MOCK (DEBUG)     = {__import__('app.services.oms', fromlist=['USE_MOCK']).USE_MOCK}")

    if args.show_request:
        show_request(args.order_no)
        return

    if args.raw:
        raw_call(args.order_no)
        return

    if args.status:
        from sqlmodel import select
        from app.models.order import Order
        with Session(engine) as session:
            order = session.exec(
                select(Order).where(
                    (Order.order_id == args.order_no)
                    | (Order.source_order_id == args.order_no)
                )
            ).first()
            if not order:
                print(f"No order with id/sourceOrderId {args.order_no!r} — aborting.")
                sys.exit(1)
            print(
                f"\nPushing status {args.status!r} to OMS for order "
                f"{order.order_id} (sourceOrderId {order.source_order_id}) ..."
            )
            # Optional shipment (QPMN webhook shape: company + epoch-ms
            # shipDate) — the service converts it to OMS's carrierName +
            # ISO-8601 shipDate, same as the production package_shipped flow.
            shipments = None
            if args.tracking:
                import time
                shipments = [{
                    "trackingNumber": args.tracking,
                    "company": args.carrier or "TEST-CARRIER",
                    # Simulate the SF Express HK tracking URL, same shape the
                    # QPMN package_shipped webhook carries.
                    "trackingUrl": (
                        "https://hk.sf-express.com/hk/tc/waybill/waybill-detail/"
                        + args.tracking
                    ),
                    "shipDate": int(time.time() * 1000),
                }]
                print(f"Shipments (QPMN shape): {shipments}")
            result = oms_service.update_order_status(
                order, args.status, status_desc=args.status_desc,
                shipments=shipments,
            )
        print("\n== Result ==")
        print(f"success:     {result.get('success')}")
        print(f"status_code: {result.get('status_code')}")
        print(f"message:     {result.get('message')}")
        print(f"error_code:  {result.get('error_code')}")
        print(f"response:    {result.get('response')}")
        print("request_payload:")
        print(json.dumps(result.get("request_payload"), ensure_ascii=False,
                         indent=2, default=str))
        return

    print(f"\nFetching addresses for orderNo={args.order_no!r} ...")
    with Session(engine) as session:
        result = oms_service.fetch_order_addresses(
            args.order_no, session, store_id=args.store_id
        )
        # Dump inside the session block: fetch_order_addresses commits,
        # which expires the Address instances — attribute access after the
        # session closes would raise DetachedInstanceError.
        print("\n== Result ==")
        dump_address("delivery", result.get("delivery"))
        dump_address("billing", result.get("billing"))
    if not result.get("delivery") and not result.get("billing"):
        print("\nNo addresses returned — check the [OMS] log lines above for")
        print("success=false / decrypt errors / timeouts, and the HUB4 notes")
        print("in docs (appSecret must match the OMS side).")


if __name__ == "__main__":
    main()
