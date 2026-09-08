"""Manually backfill daily_sales_stats rows.

Reuses the beat task's aggregation logic (app.tasks.stats.aggregate_day), so
the numbers are identical to what the nightly job produces — reruns are
idempotent upserts and never duplicate rows.

Examples:
  # Single day
  python backfill_sales_stats.py --date 2026-09-03

  # Inclusive date range
  python backfill_sales_stats.py --start-date 2026-08-01 --end-date 2026-08-31

  # Single store only
  python backfill_sales_stats.py --start-date 2026-08-01 --end-date 2026-08-31 --store-id 348681544
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select

from app.core.database import engine
from app.models.client import Client
from app.models.daily_sales_stat import DailySalesStat
from app.tasks.stats import aggregate_day


def parse_day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date '{value}' (expected YYYY-MM-DD)")


def main():
    parser = argparse.ArgumentParser(
        description="Manually backfill daily_sales_stats (same aggregation as the nightly beat task)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if __doc__ else None,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--date",
        type=parse_day,
        help="Single day to aggregate (YYYY-MM-DD)",
    )
    target.add_argument(
        "--start-date",
        type=parse_day,
        help="Range start, inclusive (YYYY-MM-DD; requires --end-date)",
    )
    parser.add_argument(
        "--end-date",
        type=parse_day,
        help="Range end, inclusive (YYYY-MM-DD; requires --start-date)",
    )
    parser.add_argument(
        "-s", "--store-id",
        type=str,
        default=None,
        help="Only aggregate orders of this store (default: all stores)",
    )
    args = parser.parse_args()

    if args.date:
        start = end = args.date
    else:
        if not args.end_date:
            parser.error("--start-date requires --end-date")
        start, end = args.start_date, args.end_date
        if start > end:
            parser.error(f"start-date {start} is after end-date {end}")

    today = datetime.now().astimezone(tz=None).date()  # local date, only used for the notice below
    if end >= today:
        print(f"[WARN] {end} is today or in the future — figures for unfinished days are partial "
              f"and will be recomputed by the nightly task.")

    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    scope = f"store '{args.store_id}'" if args.store_id else "all stores"
    print(f"[SalesStats] backfilling {len(days)} day(s) {start}..{end} for {scope}")

    with Session(engine) as session:
        # Same client map as the beat task: inactive clients keep attribution.
        clients = session.exec(select(Client)).all()
        client_by_store = {c.store_id: c for c in clients if c.store_id}
        currency_cache = {}

        total = 0
        for day in days:
            written = aggregate_day(
                session, day, client_by_store, currency_cache,
                only_store_id=args.store_id,
            )
            total += written
            print(f"    {day}: {written} stat row(s) written/updated")

        rows_query = select(DailySalesStat).where(
            DailySalesStat.stat_date >= start,
            DailySalesStat.stat_date <= end,
        )
        if args.store_id:
            rows_query = rows_query.where(DailySalesStat.store_id == args.store_id)
        rows = session.exec(rows_query).all()
        if rows:
            print("[SalesStats] resulting rows:")
            for r in sorted(rows, key=lambda r: (r.stat_date, r.store_id)):
                print(
                    f"    {r.stat_date} store={r.store_id} client={r.client_id} "
                    f"cur={r.currency} req={r.orders_requested} sub={r.orders_submitted} "
                    f"items={r.line_items_count} qty={r.line_items_quantity} amount={r.total_amount}"
                )

    print(f"[SalesStats] done: {total} row(s) written/updated across {len(days)} day(s).")


if __name__ == "__main__":
    main()
