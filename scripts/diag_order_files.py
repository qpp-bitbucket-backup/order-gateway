"""Diagnostic for push failures: dump order.files keys, client packing
flags, and per-item effect-view counts (the numbers behind the
"design file not found" check in _prepare_order_and_skus).

Usage:
    python scripts/diag_order_files.py --order-id <uuid>
"""
import argparse

from sqlmodel import Session, select

from app.core.database import engine
from app.models.client import Client
from app.models.order import Order
from app.services.sku_matching import find_sku_by_ref, design_file_key


def effect_view_count(sku_row) -> int:
    """Count views carrying effectImages in the SKU's design data."""
    source = sku_row.product_design_data or sku_row.customize_project or {}
    design_data = source.get("designData", []) if isinstance(source, dict) else []
    return sum(
        1
        for material in design_data
        for view in material.get("views", [])
        if any(d.get("effectImages") for d in (view.get("designs") or []))
    )


def main(order_id: str) -> None:
    with Session(engine) as session:
        order = session.exec(select(Order).where(Order.order_id == order_id)).first()
        if not order:
            print(f"Order not found: {order_id}")
            return
        files = order.files or {}
        print(f"order_id   : {order.order_id}")
        print(f"status     : {order.status}")
        print(f"store_id   : {order.store_id}")
        print(f"files keys : {sorted(files.keys())}")
        for key, entries in sorted(files.items()):
            urls = [(e or {}).get("url", "")[:90] for e in (entries or [])]
            print(f"  {key}: {len(entries or [])} file(s)")
            for u in urls:
                print(f"      {u}")
        label_keys = [k for k in files if k.startswith("label-")]
        if not label_keys:
            print(">> NO label-* keys — packing documents were never generated/uploaded")
        logs = order.logs if isinstance(order.logs, list) else []
        if logs:
            print("\nlast log entries:")
            for entry in logs[-5:]:
                if isinstance(entry, dict):
                    print(f"  [{entry.get('action', '?')}] {entry.get('message', '')}")
                else:
                    print(f"  {entry}")
        print()

        if order.store_id:
            client = session.exec(
                select(Client).where(Client.store_id == order.store_id)
            ).first()
            if client:
                print(f"client.enable_brand_label : {client.enable_brand_label}")
                lines = client.brand_label_lines
                print(f"client.brand_label_lines  : {len(lines)} line(s)" if lines
                      else "client.brand_label_lines  : NONE (build_label_pdf will skip)")
            else:
                print(f">> No client row for store_id {order.store_id}")
        print()

        items = (order.order_data or {}).get("items", [])
        for i, item in enumerate(items):
            sku_ref = item.get("sku")
            sku_row = find_sku_by_ref(
                session, sku_ref, active_only=False,
                match_internal_id=True, store_id=order.store_id,
            ) if sku_ref else None
            if not sku_row:
                print(f"item[{i}] sku_ref={sku_ref!r}: SKU row NOT FOUND")
                continue
            views = effect_view_count(sku_row)
            key = design_file_key(item, sku_row.sku_id, i)
            design_files = len(files.get(key, []) or [])
            label_present = f"label-{i}" in files
            need = views - 1 if label_present else views
            verdict = "OK" if design_files >= need else "NOT ENOUGH FILES"
            print(
                f"item[{i}] sku={sku_row.sku_id} files_key={key!r}: effect_views={views} "
                f"design_files={design_files} label={'yes' if label_present else 'no'} "
                f"→ need {need} design file(s): {verdict}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--order-id", required=True)
    args = parser.parse_args()
    main(args.order_id)
