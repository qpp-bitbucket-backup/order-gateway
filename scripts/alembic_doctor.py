"""Detect alembic_version vs. actual schema drift and suggest (or apply) a fix.

Schema drift happens when DDL was applied outside alembic (e.g. a DBA ran
the offline ``--sql`` script but skipped the trailing ``UPDATE
alembic_version``). A later ``alembic upgrade head`` then fails with MySQL
1060 "Duplicate column name" on a migration whose change already exists.

The doctor inspects well-known schema fingerprints, one per migration, and
derives the revision the database ACTUALLY matches. When that differs from
``alembic_version`` the fix is ``alembic stamp <detected>`` followed by
``alembic upgrade head``.

Usage (run with the TARGET environment's .env in place):
    python -m scripts.alembic_doctor          # diagnose only
    python -m scripts.alembic_doctor --fix    # stamp to the detected revision
                                              # (never touches tables itself)
"""
import sys
from typing import Callable, List, Tuple

from sqlalchemy import inspect, text

from app.core.database import engine


def _cols(insp: object, table: str) -> List[str]:
    return [c["name"] for c in insp.get_columns(table)]  # type: ignore[attr-defined]


# One fingerprint per migration, oldest first. A revision "counts" only when
# every migration before it also matched, mirroring how the chain executes.
# Keep this list in sync with alembic/versions/ — add one entry per new head.
CHECKS: List[Tuple[str, str, Callable[[object], bool]]] = [
    (
        "c9d0e1f2a3b4",
        "clients.cooling_off_seconds column",
        lambda i: "cooling_off_seconds" in _cols(i, "clients"),
    ),
    (
        "d5e6f7a8b9c0",
        "clients.notification_config column + notification_email_logs table",
        lambda i: "notification_config" in _cols(i, "clients")
        and "notification_email_logs" in i.get_table_names(),
    ),
    (
        "a2b3c4d5e6f7",
        "store-scoped unique indexes (uq_products_store_product_code / uq_skus_store_code)",
        lambda i: any(
            idx["name"] in ("uq_products_store_product_code", "uq_skus_store_code")
            for idx in (i.get_indexes("products", schema=None) or [])
            + (i.get_indexes("skus", schema=None) or [])
        ),
    ),
    (
        "b3c4d5e6f7a8",
        "clients.default_recipient_email column",
        lambda i: "default_recipient_email" in _cols(i, "clients"),
    ),
]

HEAD = CHECKS[-1][0]


def detect_actual_revision(insp: object) -> Tuple[str, List[Tuple[str, str, bool]]]:
    """Return (revision, per-check results) where revision is the newest
    migration whose fingerprint (and all earlier ones) matched."""
    results: List[Tuple[str, str, bool]] = []
    # Baseline = the revision BEFORE the first CHECKS entry (its fingerprint
    # predates the drift window; anything older is out of scope here).
    matched = "e6f7a8b9c0d1"
    for rev, desc, check in CHECKS:
        ok = check(insp)
        results.append((rev, desc, ok))
        if ok:
            matched = rev
        else:
            break
    return matched, results


def main() -> int:
    fix = "--fix" in sys.argv

    with engine.connect() as conn:
        insp = inspect(conn)
        try:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            current = None  # alembic_version table missing entirely
        # inspect() is lazy — run all fingerprint checks inside the connection
        # scope, otherwise ResourceClosedError on the first get_columns().
        actual, results = detect_actual_revision(insp)

    print(f"DB URL          : {engine.url.render_as_string(hide_password=True)}")
    print(f"alembic_version : {current or '<table missing>'}")
    print("fingerprint checks (chain order):")
    for rev, desc, ok in results:
        print(f"  [{'x' if ok else ' '}] {rev}  {desc}")
    print(f"actual schema   : {actual}")

    if current == HEAD and actual == HEAD:
        print("no drift - database and alembic_version are both at head")
        return 0

    if current == actual:
        print(f"no drift - run 'alembic upgrade head' to apply the remaining migrations")
        return 0

    print(f"DRIFT: schema matches {actual} but alembic_version says {current}")
    if not fix:
        print("suggested fix:")
        print(f"  alembic stamp {actual}")
        print("  alembic upgrade head")
        print("(or re-run with --fix to stamp now; tables are not touched)")
        return 1

    from alembic import command
    from alembic.config import Config

    print(f"stamping {actual} ...")
    command.stamp(Config("alembic.ini"), actual)
    print("stamped. now run: alembic upgrade head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
