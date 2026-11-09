#!/bin/sh
# Container entrypoint: run Alembic migrations (idempotent) before the
# main process (uvicorn / celery worker).
#
# Why retries: with a rolling update (maxSurge > 1) several replicas can
# start at once and race on alembic_version; a failed attempt retries and
# the loser finds the schema already applied and exits cleanly.
#
# Set RUN_DB_MIGRATIONS=false to skip (e.g. a deployment that must never
# touch the schema, or running against a read-only replica).
set -e

if [ "${RUN_DB_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] Running database migrations..."
    retry=0
    until alembic upgrade head; do
        retry=$((retry + 1))
        if [ "$retry" -ge 5 ]; then
            echo "[entrypoint] Migration failed after $retry attempts, aborting." >&2
            exit 1
        fi
        echo "[entrypoint] Migration attempt $retry failed (concurrent run or transient DB error) — retrying in 5s..."
        sleep 5
    done
    echo "[entrypoint] Migrations up to date."
else
    echo "[entrypoint] RUN_DB_MIGRATIONS=false — skipping migrations."
fi

exec "$@"
